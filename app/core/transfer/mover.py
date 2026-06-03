"""订阅 EVENT_TRANSFER_RECEIVED，将文件从收件箱移动到临时目录"""
import uuid
from loguru import logger
from app.events import event_bus, EVENT_TRANSFER_RECEIVED, EVENT_TRANSFER_MOVED
from app.core.cloud115.client import client_115
from app.core.transfer.scope import validate, expand_allowed_dirs
from app.database import get_db_conn


async def handle_transfer_received(share_url: str, inbox_dir_id: str, temp_dir_id: str, files: list = None, batch_task_id: str = None, **kwargs):
    """处理转存接收完成事件，将文件从收件箱移动到临时目录"""
    task_id = batch_task_id or str(uuid.uuid4())

    if not files:
        logger.warning(f"[Mover] 无文件，跳过: {share_url}")
        return

    if not validate(inbox_dir_id, temp_dir_id):
        logger.error(f"[Mover] 安全校验失败: inbox={inbox_dir_id} -> temp={temp_dir_id}")
        return

    expand_allowed_dirs(temp_dir_id)

    logger.info(f"[Mover] 开始移动 {len(files)} 个文件: inbox={inbox_dir_id} -> temp={temp_dir_id}")

    moved_files = []
    for f in files:
        file_cid = f.get("cid") or f.get("fid") or f.get("f")
        file_name = f.get("name") or f.get("n", "unknown")

        if not file_cid:
            continue

        success = await client_115.move_files([file_cid], temp_dir_id)
        if success:
            moved_files.append(f)
            logger.info(f"[Mover] 移动成功: {file_name} -> temp")
        else:
            logger.error(f"[Mover] 移动失败: {file_name}")

    logger.info(f"[Mover] 移动完成: {len(moved_files)}/{len(files)}, task_id={task_id}")

    # 如果是批次号，更新 file_count（递增）
    if batch_task_id:
        async with get_db_conn() as db:
            await db.execute(
                "UPDATE transfer_tasks SET file_count = file_count + ?, status = 'running' WHERE task_id = ?",
                (len(moved_files), task_id)
            )
            await db.commit()

    await event_bus.emit(
        EVENT_TRANSFER_MOVED,
        task_id=task_id,
        temp_dir_id=temp_dir_id,
        files=moved_files,
        share_url=share_url,
        batch_task_id=batch_task_id
    )


def init_mover():
    event_bus.subscribe(EVENT_TRANSFER_RECEIVED, handle_transfer_received)
    logger.info("[Transfer] Mover 已注册")