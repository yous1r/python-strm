from loguru import logger

from app.config import get_config
from app.core.cloud115.auth import auth_manager
from app.core.notify.manager import notify_manager
from app.events import (
    EVENT_CLOUD115_COOKIE_INVALID,
    EVENT_CLOUD115_COOKIE_RESTORED,
    event_bus,
)
from app.utils.scheduler import add_job, remove_job


AUTO_SYNC_JOB_ID = "auto_sync"
DB_SYNC_JOB_ID = "db_sync"
TELEGRAM_BACKGROUND_SYNC_JOB_ID = "telegram_background_sync"


class Cloud115RuntimeService:
    def __init__(self):
        self._available = True
        self._last_reason = ""

    def is_available(self) -> bool:
        return self._available

    def is_suspended(self) -> bool:
        return not self._available

    async def ensure_cookie_valid(self, *, source: str) -> bool:
        if self.is_suspended():
            return False
        if await auth_manager.validate_cookie():
            return True
        event_bus.emit_background(
            EVENT_CLOUD115_COOKIE_INVALID,
            source=source,
            reason="115 Cookie 已失效，请重新扫码登录。",
        )
        return False

    async def handle_operation_failure(self, *, source: str, reason: str) -> bool:
        if self.is_suspended():
            return True

        lowered = (reason or "").lower()
        auth_hints = (
            "cookie",
            "登录",
            "auth",
            "unauthorized",
            "relogin",
            "sign",
            "client not initialized",
            "failed to get share info",
        )
        if not any(hint in lowered for hint in auth_hints):
            return False

        if await auth_manager.validate_cookie():
            return False

        event_bus.emit_background(
            EVENT_CLOUD115_COOKIE_INVALID,
            source=source,
            reason="115 Cookie 已失效，请重新扫码登录。",
        )
        return True

    async def handle_cookie_invalid(self, source: str = "unknown", reason: str = "", **kwargs):
        reason_text = reason or "115 Cookie 已失效，请重新扫码登录。"
        was_available = self._available
        self._available = False
        self._last_reason = reason_text
        self.disable_background_jobs()
        logger.warning(f"[Cloud115Runtime] suspended source={source} reason={reason_text}")

        if was_available:
            await notify_manager.notify(
                title="[115] Cookie 已失效",
                content=(
                    f"来源: {source}\n"
                    f"原因: {reason_text}\n"
                    "已关闭 115 转存与 115 同步相关后台任务，重新扫码登录成功后会自动恢复。"
                ),
                message_type="error",
                group="cloud115-auth",
            )

    async def handle_cookie_restored(self, source: str = "unknown", **kwargs):
        if not await auth_manager.validate_cookie():
            logger.warning("[Cloud115Runtime] cookie restored event ignored because validation failed")
            return

        was_suspended = self.is_suspended()
        self._available = True
        self._last_reason = ""
        self.enable_background_jobs()
        logger.info(f"[Cloud115Runtime] resumed source={source}")

        if was_suspended:
            await notify_manager.notify(
                title="[115] Cookie 已恢复",
                content=(
                    f"来源: {source}\n"
                    "115 Cookie 校验通过，已重新开启 115 转存与 115 同步相关后台任务。"
                ),
                message_type="success",
                group="cloud115-auth",
            )

    def disable_background_jobs(self):
        remove_job(AUTO_SYNC_JOB_ID)
        remove_job(DB_SYNC_JOB_ID)
        remove_job(TELEGRAM_BACKGROUND_SYNC_JOB_ID)

    def enable_background_jobs(self):
        from app.core.sync.engine import sync_engine
        from app.services.cloud115_full_sync_service import cloud115_full_sync_service
        from app.services.telegram_background_service import telegram_background_sync_service

        config = get_config()
        interval_mins = config.monitor.poll_interval if getattr(config.monitor, "poll_interval", None) else 60
        add_job(sync_engine.run_sync_task, "interval", minutes=interval_mins, id=AUTO_SYNC_JOB_ID, replace_existing=True)
        add_job(
            cloud115_full_sync_service.run_scheduled_full_sync,
            "interval",
            hours=2,
            id=DB_SYNC_JOB_ID,
            replace_existing=True,
        )
        telegram_background_sync_service.configure_scheduled_sync_job()


cloud115_runtime_service = Cloud115RuntimeService()


def init_cloud115_runtime_events():
    event_bus.subscribe(EVENT_CLOUD115_COOKIE_INVALID, cloud115_runtime_service.handle_cookie_invalid)
    event_bus.subscribe(EVENT_CLOUD115_COOKIE_RESTORED, cloud115_runtime_service.handle_cookie_restored)
    logger.info("[Cloud115Runtime] 已注册 115 Cookie 运行时事件处理器")