"""转存整理管道模块 — 事件驱动架构"""
from loguru import logger
from app.config import get_config


def init_transfer_pipeline():
    """初始化转存整理管道。应在 app 启动时调用。"""
    from app.core.transfer.batch import init_batch_transfer
    from app.core.transfer.mover import init_mover
    from app.core.transfer.organizer import init_organizer
    from app.core.transfer.rollback import init_rollback
    from app.core.transfer.scope import init_scope

    config = get_config()
    transfer_cfg = config.transfer

    temp_dir_id = getattr(transfer_cfg, "temp_dir_id", "")
    archive_dir_id = getattr(transfer_cfg, "archive_dir_id", "")
    inbox_dir_id = getattr(transfer_cfg, "inbox_dir_id", "0")

    if getattr(transfer_cfg, "enabled", False) and temp_dir_id and archive_dir_id:
        init_scope(
            temp_dir_id=temp_dir_id,
            archive_dir_id=archive_dir_id,
            inbox_dir_id=inbox_dir_id,
        )
        init_mover()
        init_organizer()
    else:
        logger.info("[Transfer] 旧版转存管线未启用或目录配置不存在，跳过 mover/organizer 管线")

    init_rollback()
    init_batch_transfer()

    logger.info("[Transfer] 转存整理管道初始化完成")
    logger.info(f"[Transfer] inbox={inbox_dir_id}, temp={temp_dir_id}, archive={archive_dir_id}")
