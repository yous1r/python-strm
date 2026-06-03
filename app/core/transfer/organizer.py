"""订阅 EVENT_TRANSFER_MOVED，执行刮削+分类+移动+重命名"""
from loguru import logger
from app.events import event_bus, EVENT_TRANSFER_MOVED, EVENT_ORGANIZE_START, EVENT_ORGANIZE_FILE_DONE, EVENT_ORGANIZE_COMPLETE
from app.core.cloud115.client import client_115
from app.core.transfer.scope import validate, expand_allowed_dirs
from app.core.transfer.classifier import classify, build_archive_path
from app.core.transfer.catalog import ensure_path
from app.database import get_db_conn
from app.config import get_config


async def handle_transfer_moved(task_id: str, temp_dir_id: str, files: list, share_url: str = "", **kwargs):
    """处理文件已移动到临时目录事件，执行整理"""
    config = get_config()
    archive_dir_id = config.transfer.archive_dir_id

    if not archive_dir_id:
        logger.warning("[Organizer] 未配置归档目录")
        return

    if not validate(temp_dir_id, archive_dir_id):
        logger.error(f"[Organizer] 安全校验失败: temp={temp_dir_id} -> archive={archive_dir_id}")
        return

    expand_allowed_dirs(archive_dir_id)

    logger.info(f"[Organizer] 开始整理 {len(files)} 个文件, task_id={task_id}")

    await event_bus.emit(EVENT_ORGANIZE_START, task_id=task_id, file_count=len(files))

    # 创建任务记录
    async with get_db_conn() as db:
        await db.execute(
            """INSERT OR REPLACE INTO transfer_tasks
               (task_id, status, source_dir_id, archive_dir_id, file_count)
               VALUES (?, 'running', ?, ?, ?)""",
            (task_id, temp_dir_id, archive_dir_id, len(files))
        )
        await db.commit()

    success_count = 0
    seq = 0

    for f in files:
        seq += 1
        file_cid = f.get("cid") or f.get("fid") or f.get("f")
        file_name = f.get("name") or f.get("n", "unknown")

        if not file_cid:
            continue

        # 1. TMDB 刮削分类
        classify_result = await classify(file_name)
        if not classify_result:
            logger.warning(f"[Organizer] 分类失败，跳过: {file_name}")
            continue

        # 2. 构建归档目录路径
        path_parts = build_archive_path(classify_result)

        # 3. 逐级创建/查找目标目录
        target_dir_cid = await ensure_path(archive_dir_id, path_parts)
        if not target_dir_cid:
            logger.error(f"[Organizer] 创建目录失败: {path_parts}")
            continue

        # 4. 移动文件（move_files 返回 bool）
        source_cid = f.get("parent_cid", temp_dir_id)
        move_ok = await client_115.move_files([file_cid], target_dir_cid)
        if not move_ok:
            logger.error(f"[Organizer] 移动失败: {file_name}")
            continue

        logger.info(f"[Organizer] 移动成功: {file_name} -> {'/'.join(path_parts)}")

        # 5. 记录操作日志
        new_name = file_name  # 暂不重命名，由 MediaOrganizer 负责
        async with get_db_conn() as db:
            await db.execute(
                """INSERT INTO operation_logs
                   (task_id, seq, file_cid, file_name, new_name, source_cid, target_cid, op_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'move')""",
                (task_id, seq, file_cid, file_name, new_name, source_cid, target_dir_cid)
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
               SET status='done', success_count=?, completed_at=CURRENT_TIMESTAMP
               WHERE task_id=?""",
            (success_count, task_id)
        )
        await db.commit()

    logger.info(f"[Organizer] 整理完成: {success_count}/{len(files)}, task_id={task_id}")

    await event_bus.emit(EVENT_ORGANIZE_COMPLETE, task_id=task_id, success_count=success_count)


def init_organizer():
    event_bus.subscribe(EVENT_TRANSFER_MOVED, handle_transfer_moved)
    logger.info("[Transfer] Organizer 已注册")