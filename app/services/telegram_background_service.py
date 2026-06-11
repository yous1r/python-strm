from datetime import datetime, timedelta

from loguru import logger

from app.config import get_config
from app.services.cloud115_full_sync_service import cloud115_full_sync_service
from app.services.telegram_history_sync_service import telegram_history_sync_service
from app.utils.scheduler import add_job, get_job, remove_job


TELEGRAM_BACKGROUND_SYNC_JOB_ID = "telegram_background_sync"
DB_SYNC_JOB_ID = "db_sync"


class TelegramBackgroundSyncService:
    def configure_scheduled_sync_job(self) -> dict[str, object]:
        cfg = get_config().monitor.telegram
        history_cfg = getattr(cfg, "history_sync", None)
        # 是否启用telegram频道监控且开启自动同步
        if not cfg.enabled or not bool(getattr(history_cfg, "scheduled_enabled", False)):
            remove_job(TELEGRAM_BACKGROUND_SYNC_JOB_ID)
            logger.info("Telegram background sync job disabled")
            return {"status": "disabled", "job_id": TELEGRAM_BACKGROUND_SYNC_JOB_ID}

        interval_minutes = max(int(getattr(history_cfg, "scheduled_interval_minutes", 5) or 5), 1)
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
        history_cfg = getattr(cfg, "history_sync", None)
        if not cfg.enabled or not bool(getattr(history_cfg, "scheduled_enabled", False)):
            return {"status": "skipped", "reason": "telegram_background_sync_disabled"}
        if not cfg.api_id or not cfg.api_hash or not cfg.channels:
            return {"status": "skipped", "reason": "telegram_monitor_not_configured"}

        sync_result = telegram_history_sync_service.queue_history_sync_request(
            telegram_history_sync_service.build_request_for_schedule(),
            name="telegram_history_sync:scheduled",
        )
        return sync_result

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