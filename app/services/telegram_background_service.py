from datetime import datetime, timedelta

from loguru import logger

from app.config import get_config
from app.services.cloud115_full_sync_service import cloud115_full_sync_service
from app.services.telegram_resource_transfer_service import process_resource_transfer
from app.services.telegram_service import sync_configured_channels
from app.utils.scheduler import add_job, get_job, remove_job


TELEGRAM_BACKGROUND_SYNC_JOB_ID = "telegram_background_sync"
DB_SYNC_JOB_ID = "db_sync"


class TelegramBackgroundSyncService:
    def configure_scheduled_sync_job(self) -> dict[str, object]:
        cfg = get_config().monitor.telegram
        if not cfg.enabled or not cfg.scheduled_sync_enabled:
            remove_job(TELEGRAM_BACKGROUND_SYNC_JOB_ID)
            logger.info("Telegram background sync job disabled")
            return {"status": "disabled", "job_id": TELEGRAM_BACKGROUND_SYNC_JOB_ID}

        interval_minutes = max(int(cfg.scheduled_sync_interval_minutes or 5), 1)
        add_job(
            self.run_scheduled_sync,
            "interval",
            minutes=interval_minutes,
            id=TELEGRAM_BACKGROUND_SYNC_JOB_ID,
            replace_existing=True,
        )
        logger.info(f"Registered Telegram background sync job (every {interval_minutes}m)")
        return {"status": "enabled", "job_id": TELEGRAM_BACKGROUND_SYNC_JOB_ID, "interval_minutes": interval_minutes}

    async def run_scheduled_sync(self) -> dict[str, object]:
        cfg = get_config().monitor.telegram
        if not cfg.enabled or not cfg.scheduled_sync_enabled:
            return {"status": "skipped", "reason": "telegram_background_sync_disabled"}
        if not cfg.api_id or not cfg.api_hash or not cfg.channels:
            return {"status": "skipped", "reason": "telegram_monitor_not_configured"}

        sync_result = await sync_configured_channels(
            cfg.api_id,
            cfg.api_hash,
            bot_token=getattr(cfg, "bot_token", ""),
            proxy=cfg.proxy,
            channels=cfg.channels,
            keywords=cfg.keywords,
            emit_events=False,
            startup_mode="incremental",
            limit=max(int(cfg.scheduled_sync_limit or 20), 1),
        )
        resources = list(sync_result.get("resources", []) or [])

        transfer_results = []
        success_count = 0
        for resource in resources:
            result = await process_resource_transfer(resource, source="telegram_background")
            transfer_results.append(result)
            if result.get("status") == "success":
                success_count += 1

        full_sync_result: dict[str, object] = {"status": "skipped", "reason": "no_successful_transfers"}
        if success_count > 0:
            full_sync_result = await self._trigger_followup_full_sync(
                skip_within_minutes=max(int(cfg.full_sync_skip_if_scheduled_within_minutes or 20), 0)
            )

        return {
            "status": "success",
            "channels": sync_result.get("channels", []),
            "resources": resources,
            "resource_count": len(resources),
            "successful_transfers": success_count,
            "transfer_results": transfer_results,
            "full_sync": full_sync_result,
        }

    async def _trigger_followup_full_sync(self, *, skip_within_minutes: int) -> dict[str, object]:
        if self.is_full_sync_scheduled_within(skip_within_minutes):
            return {
                "status": "skipped",
                "reason": "db_sync_already_scheduled_soon",
                "window_minutes": skip_within_minutes,
            }
        return await cloud115_full_sync_service.start_full_sync(source="telegram_monitor")

    def is_full_sync_scheduled_within(self, window_minutes: int) -> bool:
        if window_minutes < 0:
            return False
        job = get_job(DB_SYNC_JOB_ID)
        next_run_time = getattr(job, "next_run_time", None) if job else None
        if not next_run_time:
            return False

        now = datetime.now(next_run_time.tzinfo) if getattr(next_run_time, "tzinfo", None) else datetime.now()
        delta = next_run_time - now
        return timedelta(0) <= delta <= timedelta(minutes=window_minutes)


telegram_background_sync_service = TelegramBackgroundSyncService()