from loguru import logger

from app.config import get_config
from app.core.cloud115.db_sync import sync_all_configured
from app.core.sync.engine import sync_engine
from app.services.telegram_history_sync_service import telegram_history_sync_service


async def run_startup_pipeline() -> dict[str, object]:
    config = get_config()
    pipeline = config.monitor.startup_pipeline
    results: dict[str, object] = {}

    if not pipeline.enabled:
        logger.info("Startup pipeline disabled, skipping bootstrap tasks.")
        return {"status": "skipped", "results": results}

    if pipeline.run_db_sync:
        logger.info("Running startup 115 DB sync...")
        results["db_sync"] = await sync_all_configured()

    telegram_config = config.monitor.telegram
    should_run_telegram_sync = (
        pipeline.run_telegram_sync
        and telegram_config.enabled
        and telegram_config.startup_sync != "disabled"
        and telegram_config.api_id
        and telegram_config.api_hash
        and bool(telegram_config.channels)
    )
    if should_run_telegram_sync:
        logger.info("Queueing startup Telegram history sync...")
        request = telegram_history_sync_service.build_request_for_startup()
        results["telegram_sync"] = telegram_history_sync_service.queue_history_sync_request(
            request,
            name="telegram_history_sync:startup",
        )

    if pipeline.run_strm_sync:
        logger.info("Running startup STRM sync...")
        results["strm_sync"] = await sync_engine.run_sync_task(force=False)

    return {"status": "success", "results": results}