"""115 网盘本地数据库同步：使用 p115client.updatedb 将网盘目录树缓存到本地 SQLite"""
import asyncio
import os

from loguru import logger

from app.config import get_config
from app.core.cloud115.auth import auth_manager
from app.events import EVENT_CLOUD115_DB_SYNC_COMPLETED, event_bus
from app.utils.background_tasks import CLOUD_API_POOL, get_pool_semaphore


def _get_db_path() -> str:
    """返回本地缓存数据库路径"""
    db_dir = os.path.join("data", "115cache")
    os.makedirs(db_dir, exist_ok=True)
    try:
        uid = auth_manager.client.user_id if auth_manager.client else "unknown"
    except Exception:
        uid = "unknown"
    return os.path.join(db_dir, f"115db-{uid}.db")


async def sync_directory(dir_id: str, dir_name: str = "", recursive: bool = True) -> int:
    """
    同步单个目录到本地数据库。
    返回影响的行数。
    """
    from p115client.tool.updatedb import updatedb

    db_path = _get_db_path()
    client = auth_manager.client

    if not client:
        logger.warning("[DBSync] 115 client 未初始化，跳过同步")
        return 0

    name = dir_name or dir_id
    logger.info(f"[DBSync] 开始同步目录: {name} (cid={dir_id})")

    try:
        async with get_pool_semaphore(CLOUD_API_POOL):
            count = await asyncio.to_thread(
                updatedb,
                client=client,
                dbfile=db_path,
                cid=dir_id,
                recursive=recursive,
                app="android",
                async_=False,
            )
        logger.info(f"[DBSync] 同步完成: {name}, 影响 {count} 行")
        return count
    except Exception as e:
        logger.error(f"[DBSync] 同步失败: {name}, error={e}")
        return 0


async def sync_all_configured() -> dict:
    """
    同步所有配置的目录（从 cloud115.sync_dirs 和 transfer 配置）。
    返回每个目录的同步结果。
    """
    config = get_config()
    results = {}

    # 1. 同步 sync_dirs 中配置的目录
    for sd in config.cloud115.sync_dirs:
        count = await sync_directory(sd.dir_id, sd.name)
        results[sd.name] = count
        await event_bus.emit(
            EVENT_CLOUD115_DB_SYNC_COMPLETED,
            dir_id=sd.dir_id,
            dir_name=sd.name,
            recursive=True,
            count=count,
            source="sync_all_configured",
        )

    # 2. 同步 transfer 管道中的 archive_dir
    transfer_cfg = config.transfer
    if transfer_cfg.enabled:
        archive_dir_id = getattr(transfer_cfg, "archive_dir_id", "")
        if archive_dir_id and archive_dir_id != "0":
            count = await sync_directory(archive_dir_id, "archive_dir")
            results["archive_dir"] = count
            await event_bus.emit(
                EVENT_CLOUD115_DB_SYNC_COMPLETED,
                dir_id=archive_dir_id,
                dir_name="archive_dir",
                recursive=True,
                count=count,
                source="sync_all_configured",
            )

    return results


def get_query_db():
    """获取 P115QueryDB 实例，用于本地查询（避免 115 API 调用）"""
    from p115client.tool.updatedb import P115QueryDB

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None
    try:
        return P115QueryDB(db_path)
    except Exception as e:
        logger.error(f"[DBSync] 打开查询数据库失败: {e}")
        return None


def list_local_files(dir_id: str, recursive: bool = False) -> list[dict]:
    """
    从本地数据库列出目录内容（不调用 115 API）。
    recursive=False 时仅返回直属子项；为 True 时返回整棵子树。
    返回 [{"id": int, "name": str, "parent_id": int, "is_dir": bool, "size": int, "pickcode": str, "sha": str}, ...]
    """
    qdb = get_query_db()
    if not qdb:
        return []
    try:
        cid = int(dir_id)
        base_fields = ("id", "parent_id", "name", "is_dir", "size", "pickcode")
        sha_fields = (*base_fields, "sha1")

        try:
            if recursive:
                rows = list(qdb.iter_descendants(cid, fields=sha_fields))
            else:
                rows = list(qdb.iter_children(cid, fields=sha_fields))
        except Exception:
            if recursive:
                rows = list(qdb.iter_descendants(cid, fields=base_fields))
            else:
                rows = list(qdb.iter_children(cid, fields=base_fields))

        return [
            {
                "id": r["id"],
                "parent_id": r.get("parent_id", cid),
                "name": r["name"],
                "is_dir": bool(r["is_dir"]),
                "size": r.get("size", 0),
                "pickcode": r.get("pickcode", ""),
                "sha": str(r.get("sha1") or "").upper(),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[DBSync] 本地查询失败: {e}")
        return []
    finally:
        try:
            qdb.con.close()
        except Exception:
            pass


def get_local_pickcode(file_id: str) -> str:
    """按 115 文件 ID 从本地缓存读取 pickcode。"""
    qdb = get_query_db()
    if not qdb:
        return ""

    try:
        fid = int(file_id)
        pickcode = qdb.get_pickcode(fid)
        return str(pickcode) if pickcode else ""
    except Exception as e:
        logger.debug(f"[DBSync] 读取 pickcode 失败: file_id={file_id}, error={e}")
        return ""
    finally:
        try:
            qdb.con.close()
        except Exception:
            pass


def file_exists_locally(file_id: str) -> bool:
    """检查文件是否在本地数据库中"""
    qdb = get_query_db()
    if not qdb:
        return False
    try:
        fid = int(file_id)
        return bool(qdb.has_id(fid))
    except Exception:
        return False
    finally:
        try:
            qdb.con.close()
        except Exception:
            pass


async def handle_cloud115_db_sync_completed(
    dir_id: str,
    dir_name: str = "",
    recursive: bool = True,
    count: int = 0,
    **kwargs,
):
    """115 本地目录树同步完成后，按全局输出根目录批量刷新全部 STRM。"""
    from app.core.cloud import get_cloud_plugin

    config = get_config()
    output_root = config.strm.output_dir
    base_url = getattr(config.strm, "base_url", "") or ""

    logger.info(
        f"[DBSync] 目录同步完成后开始全量刷新 STRM: root={output_root} (trigger={dir_name or dir_id}, changed={count})"
    )

    strm_stats = await get_cloud_plugin("115").strm_generator.sync_strm_files_from_manifest(
        dir_id=dir_id,
        output_dir=output_root,
        root_output_dir=output_root,
        base_url=base_url,
        archive_root="",
    )

    logger.info(
        "[DBSync] STRM 全量刷新完成: "
        f"scanned={strm_stats.get('scanned', 0)} updated={strm_stats.get('updated', 0)} "
        f"skipped={strm_stats.get('skipped', 0)} failed={strm_stats.get('failed', 0)}"
    )


def init_db_sync_events():
    """保留旧初始化入口，避免旧调用点报错。"""
    logger.info("[DBSync] 旧版 DB sync 事件处理器已停用，等待全链路编排服务接管")
