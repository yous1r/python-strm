"""还原执行器：按操作日志逆序还原文件位置和名称"""
from loguru import logger
from app.events import event_bus, EVENT_ROLLBACK_START, EVENT_ROLLBACK_FILE_DONE, EVENT_ROLLBACK_COMPLETE
from app.core.cloud115.client import client_115
from app.database import get_db_conn


async def rollback_task(task_id: str, **kwargs):
    """对指定 task_id 执行还原，按 seq DESC 逆序执行操作日志"""
    logger.info(f"[Rollback] 开始还原任务: {task_id}")

    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM operation_logs WHERE task_id=? AND status='done' ORDER BY seq DESC",
            (task_id,)
        )
        logs = await cursor.fetchall()

    if not logs:
        logger.warning(f"[Rollback] 任务 {task_id} 无操作日志")
        await event_bus.emit(EVENT_ROLLBACK_COMPLETE, task_id=task_id, rolled_back=0, total_ops=0)
        return

    total = len(logs)
    rolled_back = 0
    errors = []

    for log_row in logs:
        row = dict(log_row)
        seq = row["seq"]
        file_cid = row["file_cid"]
        file_name = row["file_name"]
        new_name = row.get("new_name")
        source_cid = row["source_cid"]
        target_cid = row["target_cid"]
        op_type = row["op_type"]

        try:
            if op_type == "move":
                # 逆向移动：从 target_cid 移回 source_cid
                ok = await client_115.move_files([file_cid], source_cid)
                if not ok:
                    errors.append(f"移动还原失败: {file_name}")
                    continue
                logger.info(f"[Rollback] 移动还原成功: {file_name} -> {source_cid}")

            elif op_type == "rename":
                # 逆向重命名：从 new_name 改回 file_name
                if new_name and new_name != file_name:
                    ok = await client_115.rename_file(file_cid, file_name)
                    if not ok:
                        errors.append(f"重命名还原失败: {file_name}")
                        continue
                    logger.info(f"[Rollback] 重命名还原: {new_name} -> {file_name}")

            elif op_type == "create_dir":
                pass  # 目录不还原

            # 更新日志状态
            async with get_db_conn() as db:
                await db.execute(
                    "UPDATE operation_logs SET status='rolled_back' WHERE task_id=? AND seq=?",
                    (task_id, seq)
                )
                await db.commit()

            rolled_back += 1

            await event_bus.emit(
                EVENT_ROLLBACK_FILE_DONE,
                task_id=task_id,
                file_cid=file_cid,
                seq=seq
            )

        except Exception as e:
            msg = f"还原异常: seq={seq}, file={file_name}, error={e}"
            logger.error(f"[Rollback] {msg}")
            errors.append(msg)

    # 更新任务状态
    status = "rolled_back" if rolled_back == total else "partial"
    async with get_db_conn() as db:
        await db.execute(
            "UPDATE transfer_tasks SET status=? WHERE task_id=?",
            (status, task_id)
        )
        await db.commit()

    logger.info(f"[Rollback] 还原完成: {rolled_back}/{total}, task_id={task_id}")

    await event_bus.emit(
        EVENT_ROLLBACK_COMPLETE,
        task_id=task_id,
        rolled_back=rolled_back,
        total_ops=total,
        errors=errors
    )


def init_rollback():
    event_bus.subscribe(EVENT_ROLLBACK_START, rollback_task)
    logger.info("[Transfer] Rollback 已注册")