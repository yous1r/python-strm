from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_start_schedules_startup_sync_in_background(monkeypatch):
    from app.core.monitor.telegram import TelegramMonitor

    class FakeClient:
        def on(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return True

        async def start(self, *args, **kwargs):
            return None

        async def disconnect(self):
            return None

    class FakeConfig:
        enabled = True
        api_id = "1"
        api_hash = "2"
        proxy = ""
        channels = ["@demo"]
        keywords = ["电影"]
        startup_sync = "latest"
        bot_token = ""

    monitor = TelegramMonitor()
    monitor.config = FakeConfig()

    monkeypatch.setattr("app.core.monitor.telegram.get_config", lambda: type("Cfg", (), {"monitor": type("M", (), {"telegram": FakeConfig()})()})())
    monkeypatch.setattr("app.core.monitor.telegram.build_telegram_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("app.core.monitor.telegram.parse_channels", lambda channels: channels)

    await monitor.start()

    assert monitor.client is not None
