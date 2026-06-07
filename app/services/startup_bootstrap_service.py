from loguru import logger

from app.config import get_config
from app.core.cloud115.db_sync import sync_all_configured
from app.core.sync.engine import sync_engine
from app.services.telegram_service import sync_configured_channels


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
        logger.info("Running startup Telegram history sync...")
        results["telegram_sync"] = await sync_configured_channels(
            telegram_config.api_id,
            telegram_config.api_hash,
            bot_token=getattr(telegram_config, "bot_token", ""),
            proxy=telegram_config.proxy,
            channels=telegram_config.channels,
            keywords=telegram_config.keywords,
            emit_events=True,
            startup_mode=getattr(telegram_config, "startup_sync", "latest"),
        )

    if pipeline.run_strm_sync:
        logger.info("Running startup STRM sync...")
        results["strm_sync"] = await sync_engine.run_sync_task(force=False)

    return {"status": "success", "results": results}