import uuid
from typing import Iterable, Optional

from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.cloud115.strm import generator_115
from app.core.transfer.classifier import classify
from app.core.transfer.placement import derive_series_scope_path
from app.core.transfer.receive_target import prepare_receive_target
from app.core.transfer.strm_manifest import list_records_for_rewrite
from app.services.transfer_destination_service import resolve_transfer_destination
from app.events import (
    EVENT_ROLLBACK_START,
    EVENT_TRANSFER_MOVED,
    EVENT_TRANSFER_RECEIVED,
    event_bus,
    spawn_task,
)
from app.database import get_db_conn
from app.utils.background_tasks import LOCAL_DB_POOL, run_in_background_pool


class TransferServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def receive_share_task(
    share_url: str,
    receive_code: str = "",
    target_dir_id: str = "",
    filter_rules: Optional[Iterable[str]] = None,
) -> dict[str, object]:
    """接收 115 分享链接并触发转存整理事件。

    非 debug 入口以 115 STRM 扫描源目录为根目录，target_dir_id 为用户确认的根目录。
    """
    config = get_config()
    transfer_cfg = config.transfer

    if not transfer_cfg.enabled:
        raise TransferServiceError("转存整理管道未启用")

    try:
        destination = resolve_transfer_destination("115", target_dir_id, config=config)
    except ValueError as exc:
        raise TransferServiceError(str(exc)) from exc
    archive_dir_id = destination["dir_id"]

    strm_cfg = config.strm

    receive_target = await prepare_receive_target(
        share_url=share_url,
        receive_code=receive_code,
        archive_dir_id=archive_dir_id,
        fallback_dir_id=archive_dir_id,
        classifier=classify,
    )
    target_dir = receive_target.target_dir_id
    if not target_dir:
        raise TransferServiceError("无法确定转存目标目录")

    result = await client_115.share_receive(
        share_url,
        receive_code,
        target_dir_id=target_dir,
        filter_rules=list(filter_rules) if filter_rules else None,
    )
    if not result.get("state"):
        raise TransferServiceError(result.get("error", "转存失败"))

    share_files = list(result.get("share_files") or receive_target.share_files)
    task_id = str(uuid.uuid4())

    if receive_target.archive_rel_path:
        generated = await run_in_background_pool(
            lambda: generator_115.generate_strm_for_folder(
                target_dir,
                share_files,
                receive_target.archive_rel_path,
                strm_cfg.output_dir,
                task_id=task_id,
            ),
            pool=LOCAL_DB_POOL,
        )
        series_scope = derive_series_scope_path(receive_target.archive_rel_path)
        strm_stats = await run_in_background_pool(
            lambda: generator_115.sync_strm_files_from_manifest(
                dir_id=target_dir,
                output_dir=strm_cfg.output_dir,
                root_output_dir=strm_cfg.output_dir,
                base_url=getattr(strm_cfg, "base_url", "") or "",
                archive_root=series_scope,
            ),
            pool=LOCAL_DB_POOL,
        )
        return {
            "status": "success",
            "task_id": task_id,
            "msg": (
                f"转存已完成，新增 {len(generated)} 个 STRM 文件，"
                f"并批量更新 {int(strm_stats.get('updated', 0) or 0)} 个 STRM 文件"
            ),
            "target_dir_id": target_dir,
            "archive_rel_path": receive_target.archive_rel_path,
            "share_files": share_files,
            "strm_updated_count": int(strm_stats.get("updated", 0) or 0),
        }

    return {
        "status": "success",
        "task_id": task_id,
        "msg": "转存已完成，未识别到可生成 STRM 的归档路径",
        "target_dir_id": target_dir,
        "share_files": share_files,
    }


async def list_transfer_tasks(status: Optional[str] = None, limit: int = 50) -> dict[str, list[dict]]:
    async with get_db_conn() as db:
        if status:
            cursor = await db.execute(
                "SELECT * FROM transfer_tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM transfer_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
    return {"tasks": [dict(row) for row in rows]}


async def get_transfer_task_detail(task_id: str) -> dict:
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM transfer_tasks WHERE task_id=?",
            (task_id,),
        )
        task = await cursor.fetchone()
        if not task:
            raise TransferServiceError("任务不存在", status_code=404)

        cursor = await db.execute(
            "SELECT * FROM operation_logs WHERE task_id=? ORDER BY seq ASC",
            (task_id,),
        )
        operations = await cursor.fetchall()

    result = dict(task)
    result["operations"] = [dict(row) for row in operations]
    return result


async def run_manual_organize_task(temp_dir_id: str) -> dict[str, object]:
    transfer_cfg = get_config().transfer
    archive_dir_id = getattr(transfer_cfg, "archive_dir_id", "")

    if not temp_dir_id:
        raise TransferServiceError("未提供临时目录")
    if not archive_dir_id or archive_dir_id == "0":
        raise TransferServiceError("旧版归档目录配置已移除，请使用 STRM 扫描源目录转存")

    files = await _list_regular_files(temp_dir_id, limit=200)
    task_id = str(uuid.uuid4())

    spawn_task(
        event_bus.emit(
            EVENT_TRANSFER_MOVED,
            task_id=task_id,
            temp_dir_id=temp_dir_id,
            files=files,
        ),
        name="transfer_organize_manual",
    )

    return {
        "status": "success",
        "task_id": task_id,
        "msg": f"整理任务已启动，共 {len(files)} 个文件",
    }


async def start_rollback_task(task_id: str) -> dict[str, str]:
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT status FROM transfer_tasks WHERE task_id=?",
            (task_id,),
        )
        task = await cursor.fetchone()

    if not task:
        raise TransferServiceError("任务不存在", status_code=404)
    if task["status"] == "rolled_back":
        raise TransferServiceError("任务已还原")

    event_bus.emit_background(EVENT_ROLLBACK_START, task_id=task_id)
    return {"status": "success", "task_id": task_id, "msg": "还原任务已启动"}


async def preview_rollback_task(task_id: str) -> dict[str, object]:
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM operation_logs WHERE task_id=? AND status='done' ORDER BY seq DESC",
            (task_id,),
        )
        operations = await cursor.fetchall()

    return {
        "task_id": task_id,
        "total_ops": len(operations),
        "operations": [dict(row) for row in operations],
    }


async def overwrite_task_strm(task_id: str) -> dict[str, object]:
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT task_id, status FROM transfer_tasks WHERE task_id=?",
            (task_id,),
        )
        task = await cursor.fetchone()
        if not task:
            raise TransferServiceError("任务不存在", status_code=404)

        cursor = await db.execute(
            """
            SELECT file_id, strm_path, strm_abs_path, strm_rel_path, play_identity
            FROM strm_records
            WHERE task_id=? AND cloud_type='115'
            ORDER BY id ASC
            """,
            (task_id,),
        )
        records = [dict(row) for row in await cursor.fetchall()]

    if not records:
        raise TransferServiceError("该任务暂无可覆盖的 STRM 记录", status_code=404)

    rewritten = await run_in_background_pool(
        lambda: generator_115.rewrite_manifest_records(records),
        pool=LOCAL_DB_POOL,
    )
    return {
        "status": "success",
        "task_id": task_id,
        "rewritten_count": len(rewritten),
        "files": rewritten[:10],
        "msg": f"已覆盖 {len(rewritten)} 个 STRM 文件",
    }


async def rewrite_archive_strm(archive_root: str = "") -> dict[str, object]:
    records = await list_records_for_rewrite(archive_root)
    if not records:
        raise TransferServiceError("当前范围内暂无可覆盖的 STRM 记录", status_code=404)

    result = await run_in_background_pool(
        lambda: generator_115.rewrite_from_manifest(archive_root),
        pool=LOCAL_DB_POOL,
    )
    return {
        "status": "success",
        **result,
        "msg": f"已覆盖 {result['rewritten']} 个 STRM 文件",
    }


def get_transfer_categories() -> dict[str, list[dict[str, object]]]:
    categories = get_config().transfer.categories
    if not categories:
        categories = get_config().transfer.default_categories()

    return {
        "categories": [
            {"name": category.name, "subcategories": category.subcategories}
            for category in categories
        ]
    }


async def _list_regular_files(dir_id: str, limit: int) -> list[dict[str, str]]:
    files_res = await client_115.list_files(dir_id, limit=limit)
    if files_res.get("error"):
        return []

    files: list[dict[str, str]] = []
    for item in files_res.get("items", []):
        if item.get("is_dir"):
            continue
        files.append(
            {
                "cid": item.get("cid") or item.get("fid"),
                "name": item.get("n"),
                "parent_cid": dir_id,
            }
        )
    return files
