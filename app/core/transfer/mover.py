"""订阅 EVENT_TRANSFER_RECEIVED，将文件从收件箱移动到临时目录 — 带防呆校验"""
import uuid
import asyncio
from loguru import logger
from app.events import event_bus, EVENT_TRANSFER_RECEIVED, EVENT_TRANSFER_MOVED
from app.core.cloud115.client import client_115
from app.core.transfer.scope import validate, expand_allowed_dirs
from app.database import get_db_conn


class MoverIntegrityError(Exception):
    """移动操作完整性校验失败，中止所有后续操作"""
    pass


async def handle_transfer_received(share_url: str, inbox_dir_id: str, temp_dir_id: str, files: list = None, batch_task_id: str = None, **kwargs):
    """处理转存接收完成事件，将文件从收件箱移动到临时目录"""
    task_id = batch_task_id or str(uuid.uuid4())

    if not files:
        logger.warning(f"[Mover] 无文件，跳过: {share_url}")
        return

    if not validate(inbox_dir_id, temp_dir_id):
        logger.error(f"[Mover] 安全校验失败: inbox={inbox_dir_id} -> temp={temp_dir_id}")
        return

    if not temp_dir_id or temp_dir_id == "0":
        raise MoverIntegrityError(
            f"[Mover] temp_dir_id 不能为根目录(0)。请在转存管道配置中设置正确的临时目录cid。"
        )

    expand_allowed_dirs(temp_dir_id)

    logger.info(f"[Mover] 开始移动 {len(files)} 个文件: 收件箱(inbox={inbox_dir_id}) → 临时目录(temp={temp_dir_id})")

    # 先列出收件箱中的所有文件cid，用于移动前校验
    inbox_files = {}
    try:
        list_res = await client_115.list_files(inbox_dir_id, limit=200)
        if not list_res.get("error"):
            for item in list_res.get("items", []):
                cid = item.get("cid") or item.get("fid")
                if cid:
                    inbox_files[cid] = item.get("n", "")
    except Exception as e:
        logger.error(f"[Mover] 列出收件箱文件失败: {e}")

    moved_files = []
    for i, f in enumerate(files):
        file_cid = str(f.get("cid") or f.get("fid") or f.get("f") or "")
        file_name = f.get("name") or f.get("n", "unknown")

        if not file_cid:
            logger.warning(f"[Mover] 跳过无效文件: {file_name} (无cid)")
            continue

        # 防呆：校验文件确实在收件箱中
        if inbox_files and file_cid not in inbox_files:
            raise MoverIntegrityError(
                f"[Mover] 文件不在收件箱中！file={file_name} (cid={file_cid}) "
                f"不在 inbox_dir_id={inbox_dir_id} 的文件列表中。"
                f"可能已被移动或收件箱cid配置错误。中止！"
            )

        if i > 0:
            await asyncio.sleep(1.5)

        logger.info(f"[Mover] 移动: {file_name} (cid={file_cid}) 从 inbox={inbox_dir_id} → temp={temp_dir_id}")

        success = await client_115.move_files([file_cid], temp_dir_id)
        if not success:
            raise MoverIntegrityError(
                f"[Mover] 移动失败！{file_name} (cid={file_cid}) "
                f"无法从 {inbox_dir_id} 移动到 {temp_dir_id}。中止！"
            )

        moved_files.append(f)
        logger.info(f"[Mover] ✓ 移动完成: {file_name} (cid={file_cid})")

    logger.info(f"[Mover] 移动完成: {len(moved_files)}/{len(files)}, task_id={task_id}")

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