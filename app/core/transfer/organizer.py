"""订阅 EVENT_TRANSFER_MOVED，执行刮削+分类+移动+重命名 — 带防呆校验"""
import asyncio
from loguru import logger
from app.events import event_bus, EVENT_TRANSFER_MOVED, EVENT_ORGANIZE_START, EVENT_ORGANIZE_FILE_DONE, EVENT_ORGANIZE_COMPLETE
from app.core.cloud115.client import client_115
from app.core.transfer.scope import validate, expand_allowed_dirs
from app.core.transfer.classifier import classify, build_archive_path
from app.core.transfer.catalog import ensure_path, CatalogIntegrityError
from app.database import get_db_conn
from app.config import get_config


class OrganizerIntegrityError(Exception):
    """整理操作完整性校验失败，中止所有后续操作"""
    pass


async def handle_transfer_moved(task_id: str, temp_dir_id: str, files: list, share_url: str = "", **kwargs):
    """处理文件已移动到临时目录事件，执行整理"""
    config = get_config()
    archive_dir_id = config.transfer.archive_dir_id

    if not archive_dir_id or archive_dir_id == "0":
        logger.error("[Organizer] 归档目录未正确配置（为空或为根目录0），拒绝操作以免污染根目录")
        return

    if not validate(temp_dir_id, archive_dir_id):
        logger.error(f"[Organizer] 安全校验失败: temp={temp_dir_id} -> archive={archive_dir_id}")
        return

    if not temp_dir_id or temp_dir_id == "0":
        logger.error("[Organizer] temp_dir_id 不能为根目录(0)，拒绝操作")
        return

    expand_allowed_dirs(archive_dir_id)

    logger.info(f"[Organizer] 开始整理 {len(files)} 个文件, task_id={task_id}")
    logger.info(f"[Organizer] 临时目录: {temp_dir_id}, 归档根目录: {archive_dir_id}")

    await event_bus.emit(EVENT_ORGANIZE_START, task_id=task_id, file_count=len(files))

    # 先获取临时目录中所有文件的cid，用于移动前校验
    temp_files = {}
    try:
        list_res = await client_115.list_files(temp_dir_id, limit=500)
        if not list_res.get("error"):
            for item in list_res.get("items", []):
                cid = str(item.get("cid") or item.get("fid") or "")
                if cid:
                    temp_files[cid] = item.get("n", "")
    except Exception as e:
        logger.error(f"[Organizer] 列出临时目录失败: {e}")

    # 任务记录
    async with get_db_conn() as db:
        cursor = await db.execute("SELECT id FROM transfer_tasks WHERE task_id = ?", (task_id,))
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE transfer_tasks SET status = 'running', archive_dir_id = ? WHERE task_id = ?",
                (archive_dir_id, task_id)
            )
        else:
            await db.execute(
                """INSERT INTO transfer_tasks
                   (task_id, status, source_dir_id, archive_dir_id, file_count)
                   VALUES (?, 'running', ?, ?, ?)""",
                (task_id, temp_dir_id, archive_dir_id, len(files))
            )
        await db.commit()

    success_count = 0
    seq = 0

    for i, f in enumerate(files):
        seq += 1
        if i > 0:
            await asyncio.sleep(1.5)

        file_cid = str(f.get("cid") or f.get("fid") or f.get("f") or "")
        file_name = f.get("name") or f.get("n", "unknown")

        if not file_cid:
            logger.warning(f"[Organizer] 跳过无效文件: {file_name}")
            continue

        # 防呆：校验文件确实在临时目录中
        if temp_files and file_cid not in temp_files:
            raise OrganizerIntegrityError(
                f"[Organizer] 文件不在临时目录中！{file_name} (cid={file_cid}) "
                f"不在 temp_dir_id={temp_dir_id} 的文件列表中。"
                f"可能文件已被移动或临时目录配置错误。中止！"
            )

        # 1. TMDB 刮削分类
        classify_result = await classify(file_name)
        if not classify_result:
            logger.warning(f"[Organizer] 分类失败，跳过: {file_name}")
            continue

        # 2. 构建归档目录路径
        path_parts = build_archive_path(classify_result)
        archive_path_str = "/".join(path_parts)

        logger.info(
            f"[Organizer] [{seq}/{len(files)}] {file_name} (cid={file_cid}) "
            f"分类: {classify_result.category}/{classify_result.subcategory} "
            f"→ 目标路径: {archive_path_str}"
        )

        # 3. 逐级创建/查找目标目录（带防呆校验）
        try:
            target_dir_cid = await ensure_path(archive_dir_id, path_parts)
        except CatalogIntegrityError as e:
            raise OrganizerIntegrityError(
                f"[Organizer] 目录创建校验失败，处理文件 {file_name} 时中止。"
                f"原因: {e}"
            )

        if not target_dir_cid or target_dir_cid == "0":
            raise OrganizerIntegrityError(
                f"[Organizer] 目标目录cid异常！{file_name} → "
                f"ensure_path 返回 cid={target_dir_cid}。中止！"
            )

        logger.info(
            f"[Organizer] 移动: {file_name} (cid={file_cid}) "
            f"从 {temp_dir_id} → {target_dir_cid} ({archive_path_str})"
        )

        # 4. 移动文件
        move_ok = await client_115.move_files([file_cid], target_dir_cid)
        if not move_ok:
            raise OrganizerIntegrityError(
                f"[Organizer] 移动失败！{file_name} (cid={file_cid}) "
                f"无法从 {temp_dir_id} 移动到 {target_dir_cid}。中止！"
            )

        logger.info(f"[Organizer] ✓ 移动成功: {file_name} → {archive_path_str}")

        # 5. 记录操作日志
        new_name = file_name
        async with get_db_conn() as db:
            await db.execute(
                """INSERT INTO operation_logs
                   (task_id, seq, file_cid, file_name, new_name, source_cid, target_cid, op_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'move')""",
                (task_id, seq, file_cid, file_name, new_name, temp_dir_id, target_dir_cid)
            )
            await db.commit()

        success_count += 1

        await event_bus.emit(
            EVENT_ORGANIZE_FILE_DONE,
            task_id=task_id,
            file_cid=file_cid,
            file_name=file_name
        )

    # 更新任务状态
    async with get_db_conn() as db:
        await db.execute(
            """UPDATE transfer_tasks
               SET status='done', success_count = success_count + ?, completed_at=CURRENT_TIMESTAMP
               WHERE task_id=?""",
            (success_count, task_id)
        )
        await db.commit()

    logger.info(f"[Organizer] 整理完成: {success_count}/{len(files)}, task_id={task_id}")

    await event_bus.emit(EVENT_ORGANIZE_COMPLETE, task_id=task_id, success_count=success_count)


def init_organizer():
    event_bus.subscribe(EVENT_TRANSFER_MOVED, handle_transfer_moved)
    logger.info("[Transfer] Organizer 已注册")