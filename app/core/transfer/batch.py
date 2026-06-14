import asyncio
import os
import re

from loguru import logger

from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.cloud115.db_sync import sync_directory
from app.core.cloud115.strm import generator_115
from app.core.media.parser import parse_filename
from app.core.notify.manager import notify_manager
from app.core.transfer.classifier import build_archive_path, classify
from app.core.transfer.placement import derive_series_scope_path
from app.database import get_db_conn
from app.utils.background_tasks import LOCAL_DB_POOL, run_in_background_pool
from app.events import (
    EVENT_STRM_BATCH_COMPLETED,
    EVENT_STRM_BATCH_REQUESTED,
    EVENT_STRM_BATCH_REWRITE_REQUESTED,
    EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED,
    EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED,
    EVENT_TRANSFER_BATCH_DONE,
    EVENT_TRANSFER_BATCH_ITEM_DONE,
    EVENT_TRANSFER_BATCH_ITEM_FAILED,
    EVENT_TRANSFER_BATCH_PREPARED,
    EVENT_TRANSFER_BATCH_REQUESTED,
    event_bus,
)


_batch_states: dict[str, dict] = {}
_batch_completion_states: dict[str, dict] = {}
_batch_semaphore = asyncio.Semaphore(1)


def _clean_batch_title(title: str) -> str:
    if not title:
        return ""
    return re.sub(r'^\s*[\U0001F300-\U0001FAFF\u2600-\u27BF]+\s*', '', title).strip()


def _build_batch_sample_name(batch_title: str, row_title: str) -> str:
    """构造用于批量预分类的样本名，优先使用整剧标题，避免单集展示文案污染分类。"""
    cleaned_title = _clean_batch_title(batch_title)
    raw_title = row_title or batch_title or ""
    media_info = parse_filename(raw_title)

    if cleaned_title:
        sample_name = cleaned_title
        if media_info.year:
            sample_name += f" ({media_info.year})"
        if media_info.media_type == "episode":
            season = media_info.season or 1
            episode = media_info.episode or 1
            sample_name += f" S{season:02d}E{episode:02d}"
        return sample_name

    return raw_title


async def handle_batch_requested(task_id: str, rows: list, base_title: str = "", **kwargs):
    if not rows:
        logger.warning(f"[Batch] 空批次，忽略 task_id={task_id}")
        return

    batch_title = base_title or rows[0].get("base_title") or "批量转存"
    episode_count = len(rows)
    cloud_type = str(kwargs.get("cloud_type") or "115")
    destination_dir_id = str(
        kwargs.get("destination_dir_id")
        or kwargs.get("target_dir_id")
        or ""
    ).strip()
    target_dir_name = str(kwargs.get("target_dir_name") or "")
    series_folder_id = ""
    series_path_str = ""

    sample_name = _build_batch_sample_name(batch_title, rows[0].get("title") or "")
    try:
        classify_result = await classify(sample_name)
        if classify_result:
            path_parts = build_archive_path(classify_result)
            series_path_str = "/".join(path_parts)
            if destination_dir_id and destination_dir_id != "0":
                res = await client_115.create_path(destination_dir_id, series_path_str)
                if "id" in res and res["id"]:
                    series_folder_id = res["id"]
                    logger.info(f"[Batch] 已创建归档路径: {series_path_str} (cid={series_folder_id})")
                else:
                    logger.warning(f"[Batch] create_path 未返回目录 ID: {res}")
            else:
                logger.warning("[Batch] 未选择 STRM 远程扫描源目录，跳过目录创建")
    except Exception as exc:
        logger.error(f"[Batch] 预创建归档路径失败: {exc}")

    final_target_dir_id = series_folder_id or destination_dir_id
    _batch_states[task_id] = {
        "task_id": task_id,
        "title": batch_title,
        "episode_count": episode_count,
        "cloud_type": cloud_type,
        "destination_dir_id": destination_dir_id,
        "target_dir_name": target_dir_name,
        "series_folder_id": series_folder_id,
        "target_dir_id": final_target_dir_id,
        "series_path_str": series_path_str,
        "share_files": [],
        "success_count": 0,
        "failed_links": [],
        "group": f"transfer-batch:{batch_title}",
    }

    await event_bus.emit(
        EVENT_TRANSFER_BATCH_PREPARED,
        task_id=task_id,
        rows=rows,
        batch_title=batch_title,
        episode_count=episode_count,
        series_folder_id=series_folder_id,
        target_dir_id=final_target_dir_id,
        destination_dir_id=destination_dir_id,
        target_dir_name=target_dir_name,
        cloud_type=cloud_type,
        series_path_str=series_path_str,
    )


async def handle_batch_prepared(
    task_id: str,
    rows: list,
    batch_title: str,
    episode_count: int,
    series_folder_id: str = "",
    target_dir_id: str = "",
    destination_dir_id: str = "",
    series_path_str: str = "",
    **kwargs,
):
    state = _batch_states.get(task_id) or {}
    target_dir_id = target_dir_id or series_folder_id or state.get("target_dir_id") or destination_dir_id

    if not target_dir_id or target_dir_id == "0":
        error_message = "未选择 STRM 远程扫描源目录"
        for row in rows:
            await event_bus.emit(
                EVENT_TRANSFER_BATCH_ITEM_FAILED,
                task_id=task_id,
                db_id=row.get("id"),
                share_url=row.get("link", ""),
                error_message=error_message,
                episode_count=episode_count,
            )
        return

    async with _batch_semaphore:
        for index, row in enumerate(rows):
            share_url = row.get("link", "")
            receive_code = row.get("password", "")
            if index > 0:
                await asyncio.sleep(0.1)

            try:
                transfer_res = await client_115.share_receive(
                    share_url,
                    receive_code,
                    target_dir_id,
                    filter_rules=None,
                )
                await asyncio.sleep(3)

                if transfer_res.get("state"):
                    await event_bus.emit(
                        EVENT_TRANSFER_BATCH_ITEM_DONE,
                        task_id=task_id,
                        db_id=row.get("id"),
                        share_url=share_url,
                        share_files=transfer_res.get("share_files") or [],
                        episode_count=episode_count,
                    )
                else:
                    await event_bus.emit(
                        EVENT_TRANSFER_BATCH_ITEM_FAILED,
                        task_id=task_id,
                        db_id=row.get("id"),
                        share_url=share_url,
                        error_message=transfer_res.get("error", "转存失败"),
                        episode_count=episode_count,
                    )
            except Exception as exc:
                await event_bus.emit(
                    EVENT_TRANSFER_BATCH_ITEM_FAILED,
                    task_id=task_id,
                    db_id=row.get("id"),
                    share_url=share_url,
                    error_message=str(exc),
                    episode_count=episode_count,
                )


async def handle_batch_item_done(task_id: str, db_id: int | None, share_url: str, share_files: list, episode_count: int, **kwargs):
    state = _batch_states.get(task_id)
    if not state:
        return

    state["success_count"] += 1
    state["share_files"].extend(share_files or [])
    await _update_tg_status(db_id, "success")
    await _maybe_finish_batch(task_id, episode_count)


async def handle_batch_item_failed(task_id: str, db_id: int | None, share_url: str, error_message: str, episode_count: int, **kwargs):
    state = _batch_states.get(task_id)
    if not state:
        return

    state["failed_links"].append({"url": share_url, "error": error_message})
    await _update_tg_status(db_id, "failed")
    await _maybe_finish_batch(task_id, episode_count)


async def _maybe_finish_batch(task_id: str, episode_count: int):
    state = _batch_states.get(task_id)
    if not state:
        return

    processed = state["success_count"] + len(state["failed_links"])
    logger.info(f"[Batch] {task_id}: processed {processed}/{episode_count}")
    if processed < episode_count:
        return

    await event_bus.emit(EVENT_TRANSFER_BATCH_DONE, task_id=task_id, batch_state=state)


async def handle_batch_done(task_id: str, batch_state: dict, **kwargs):
    try:
        await _finalize_transfer_task(task_id, batch_state)

        archive_dir_id = batch_state.get("target_dir_id") or batch_state.get("series_folder_id", "")
        archive_rel_path = batch_state.get("series_path_str", "")
        if batch_state.get("success_count", 0) > 0 and archive_dir_id:
            _batch_completion_states[task_id] = dict(batch_state)
            await event_bus.emit(
                EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED,
                task_id=task_id,
                archive_dir_id=archive_dir_id,
                archive_rel_path=archive_rel_path,
                strm_rel_dir=archive_rel_path,
                batch_title=batch_state.get("title") or task_id,
                files=batch_state.get("share_files", []),
            )

        await _notify_batch_summary(task_id, batch_state)
    finally:
        _batch_states.pop(task_id, None)


async def handle_batch_db_sync_requested(
    task_id: str,
    archive_dir_id: str,
    archive_rel_path: str = "",
    strm_rel_dir: str = "",
    batch_title: str = "",
    files: list | None = None,
    **kwargs,
):
    if not archive_dir_id:
        logger.warning(f"[Batch] {task_id}: archive_dir_id 为空，跳过批次 db_sync")
        return

    dir_name = archive_rel_path or batch_title or archive_dir_id
    count = await sync_directory(archive_dir_id, dir_name, recursive=True)
    await event_bus.emit(
        EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED,
        task_id=task_id,
        archive_dir_id=archive_dir_id,
        archive_rel_path=archive_rel_path,
        strm_rel_dir=strm_rel_dir or archive_rel_path,
        batch_title=batch_title,
        files=files or [],
        count=count,
    )


async def handle_strm_batch_requested(
    task_id: str,
    archive_dir_id: str,
    files: list,
    strm_rel_dir: str = "",
    archive_rel_path: str = "",
    folder_cid: str = "",
    share_files: list | None = None,
    strm_subdir: str = "",
    **kwargs,
):
    config = get_config()
    target_dir_id = archive_dir_id or folder_cid
    target_subdir = strm_rel_dir or archive_rel_path or strm_subdir
    if not target_dir_id:
        logger.warning(f"[Batch] {task_id}: 未找到目标目录，跳过 STRM 刷新")
        return

    output_dir = os.path.join(config.strm.output_dir, target_subdir) if target_subdir else config.strm.output_dir
    manifest_stats = await run_in_background_pool(
        lambda: generator_115.sync_manifest_records(
            dir_id=target_dir_id,
            dir_name=target_subdir or target_dir_id,
            output_dir=output_dir,
            base_url=config.strm.base_url,
            recursive=True,
            root_output_dir=config.strm.output_dir,
            preserve_existing_structure=True,
        ),
        pool=LOCAL_DB_POOL,
    )
    await event_bus.emit(
        EVENT_STRM_BATCH_REWRITE_REQUESTED,
        task_id=task_id,
        archive_dir_id=target_dir_id,
        archive_rel_path=target_subdir,
        files=files or share_files or [],
        manifest_stats=manifest_stats,
    )
    logger.info(
        f"[Batch] {task_id}: manifest 刷新完成 scanned={manifest_stats.get('scanned', 0)} "
        f"manifest_changed={manifest_stats.get('changed', False)}"
    )


async def handle_strm_batch_rewrite_requested(
    task_id: str,
    archive_dir_id: str,
    files: list,
    archive_rel_path: str = "",
    manifest_stats: dict | None = None,
    **kwargs,
):
    config = get_config()
    target_dir_id = archive_dir_id
    target_subdir = archive_rel_path
    if not target_dir_id:
        logger.warning(f"[Batch] {task_id}: 未找到目标目录，跳过 STRM 重写")
        return

    output_dir = os.path.join(config.strm.output_dir, target_subdir) if target_subdir else config.strm.output_dir
    series_scope = derive_series_scope_path(target_subdir)
    strm_stats = await run_in_background_pool(
        lambda: generator_115.sync_strm_files_from_manifest(
            dir_id=target_dir_id,
            output_dir=output_dir,
            root_output_dir=config.strm.output_dir,
            base_url=config.strm.base_url,
            archive_root=series_scope,
        ),
        pool=LOCAL_DB_POOL,
    )
    await event_bus.emit(
        EVENT_STRM_BATCH_COMPLETED,
        task_id=task_id,
        archive_dir_id=target_dir_id,
        archive_rel_path=target_subdir,
        files=files or [],
        manifest_stats=manifest_stats or {},
        strm_stats=strm_stats,
    )
    logger.info(
        f"[Batch] {task_id}: db_sync 后 STRM 重写完成 updated={strm_stats.get('updated', 0)} "
        f"skipped={strm_stats.get('skipped', 0)} failed={strm_stats.get('failed', 0)}"
    )


async def handle_strm_batch_completed(
    task_id: str,
    archive_dir_id: str,
    archive_rel_path: str = "",
    files: list | None = None,
    manifest_stats: dict | None = None,
    strm_stats: dict | None = None,
    **kwargs,
):
    batch_state = _batch_completion_states.pop(task_id, {})
    failed_count = len(batch_state.get("failed_links", []))
    strm_failed_count = int((strm_stats or {}).get("failed", 0) or 0)
    status = "failed" if failed_count or strm_failed_count else "done"
    await _complete_transfer_task(task_id, status)
    logger.info(
        f"[Batch] {task_id}: 批次入库工作流完成 status={status}, "
        f"archive_dir_id={archive_dir_id}, archive_rel_path={archive_rel_path}"
    )


async def handle_batch_db_sync_completed(
    task_id: str,
    archive_dir_id: str,
    archive_rel_path: str = "",
    strm_rel_dir: str = "",
    files: list | None = None,
    count: int = 0,
    **kwargs,
):
    logger.info(f"[Batch] {task_id}: db_sync 完成，archive_dir_id={archive_dir_id}, changed={count}")
    await event_bus.emit(
        EVENT_STRM_BATCH_REQUESTED,
        task_id=task_id,
        cloud_type="115",
        archive_dir_id=archive_dir_id,
        archive_rel_path=archive_rel_path,
        strm_rel_dir=strm_rel_dir or archive_rel_path,
        files=files or [],
    )


async def _finalize_transfer_task(task_id: str, batch_state: dict):
    failed_count = len(batch_state.get("failed_links", []))
    success_count = int(batch_state.get("success_count", 0) or 0)
    status = "running" if success_count > 0 else "failed"
    error_detail = None
    if failed_count:
        error_detail = "; ".join(
            f"{item['url']}|{item['error']}" for item in batch_state["failed_links"][:3]
        )

    async with get_db_conn() as db:
        completed_sql = "" if success_count > 0 else ", completed_at=CURRENT_TIMESTAMP"
        await db.execute(
            f"""UPDATE transfer_tasks
               SET status=?, success_count=?, file_count=?, archive_dir_id=?, error_detail=?{completed_sql}
               WHERE task_id=?""",
            (
                status,
                success_count,
                batch_state.get("episode_count", 0),
                batch_state.get("target_dir_id") or batch_state.get("series_folder_id") or "library_batch",
                error_detail,
                task_id,
            ),
        )
        await db.commit()


async def _complete_transfer_task(task_id: str, status: str):
    async with get_db_conn() as db:
        await db.execute(
            "UPDATE transfer_tasks SET status=?, completed_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (status, task_id),
        )
        await db.commit()


async def _update_tg_status(db_id, status: str):
    if not db_id:
        return
    async with get_db_conn() as db:
        await db.execute("UPDATE tg_resources SET status = ? WHERE id = ?", (status, db_id))
        await db.commit()


async def _notify_batch_summary(task_id: str, batch_state: dict):
    total = batch_state.get("episode_count", 0)
    success_count = batch_state.get("success_count", 0)
    failed_items = batch_state.get("failed_links", [])
    failed_count = len(failed_items)
    title = batch_state.get("title") or task_id
    group = batch_state.get("group") or f"transfer-batch:{task_id}"
    series_path = batch_state.get("series_path_str", "")

    lines = [
        f"批次: {title}",
        f"任务ID: {task_id}",
        f"总数: {total}",
        f"成功: {success_count}",
        f"失败: {failed_count}",
    ]
    if series_path:
        lines.append(f"归档路径: {series_path}")
    if failed_items:
        lines.append("失败示例:")
        for item in failed_items[:3]:
            lines.append(f"- {item['url']} | {item['error']}")

    await notify_manager.notify(
        title="[STRM] 批量转存完成" if failed_count == 0 else "[STRM] 批量转存完成（含失败）",
        content="\n".join(lines),
        message_type="success" if failed_count == 0 else "warning",
        subtitle=f"成功 {success_count}/{total}",
        group=group,
        is_archive=True,
    )


def init_batch_transfer():
    event_bus.subscribe(EVENT_TRANSFER_BATCH_REQUESTED, handle_batch_requested)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_PREPARED, handle_batch_prepared)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_ITEM_DONE, handle_batch_item_done)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_ITEM_FAILED, handle_batch_item_failed)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_DONE, handle_batch_done)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED, handle_batch_db_sync_requested)
    event_bus.subscribe(EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED, handle_batch_db_sync_completed)
    event_bus.subscribe(EVENT_STRM_BATCH_REQUESTED, handle_strm_batch_requested)
    event_bus.subscribe(EVENT_STRM_BATCH_REWRITE_REQUESTED, handle_strm_batch_rewrite_requested)
    event_bus.subscribe(EVENT_STRM_BATCH_COMPLETED, handle_strm_batch_completed)
    logger.info("[Transfer] Batch transfer 已注册")
