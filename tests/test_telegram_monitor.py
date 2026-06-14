from unittest.mock import AsyncMock
import sqlite3

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


@pytest.mark.asyncio
async def test_start_retries_when_telethon_session_database_is_locked(monkeypatch):
    from app.core.monitor.telegram import TelegramMonitor

    disconnects = []

    class FakeClient:
        def __init__(self, *, locked: bool):
            self.locked = locked
            self.connected = False

        def on(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        async def connect(self):
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            self.connected = True

        async def is_user_authorized(self):
            return True

        async def start(self, *args, **kwargs):
            return None

        async def disconnect(self):
            disconnects.append(self.locked)

    class FakeConfig:
        enabled = True
        api_id = "1"
        api_hash = "2"
        proxy = ""
        channels = ["@demo"]
        keywords = []
        startup_sync = "latest"
        bot_token = ""

    clients = [FakeClient(locked=True), FakeClient(locked=False)]

    monitor = TelegramMonitor()
    monkeypatch.setattr("app.core.monitor.telegram.get_config", lambda: type("Cfg", (), {"monitor": type("M", (), {"telegram": FakeConfig()})()})())
    monkeypatch.setattr("app.core.monitor.telegram.build_telegram_client", lambda *args, **kwargs: clients.pop(0))
    monkeypatch.setattr("app.core.monitor.telegram.parse_channels", lambda channels: channels)
    monkeypatch.setattr("app.core.monitor.telegram.TELEGRAM_SESSION_RETRY_DELAY_SECONDS", 0)

    await monitor.start()

    assert monitor.client is not None
    assert monitor.client.connected is True
    assert disconnects == [True]


@pytest.mark.asyncio
async def test_start_force_closes_session_connection_when_disconnect_is_locked(monkeypatch):
    from app.core.monitor.telegram import TelegramMonitor

    class FakeRawConnection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeSession:
        def __init__(self):
            self.raw_connection = FakeRawConnection()
            self._conn = self.raw_connection

    class FakeClient:
        def __init__(self, *, locked: bool):
            self.locked = locked
            self.connected = False
            self.session = FakeSession()

        def on(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        async def connect(self):
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            self.connected = True

        async def is_user_authorized(self):
            return True

        async def start(self, *args, **kwargs):
            return None

        async def disconnect(self):
            if self.locked:
                raise sqlite3.OperationalError("database is locked")

    class FakeConfig:
        enabled = True
        api_id = "1"
        api_hash = "2"
        proxy = ""
        channels = ["@demo"]
        keywords = []
        startup_sync = "latest"
        bot_token = ""

    clients = [FakeClient(locked=True), FakeClient(locked=False)]
    built_clients = []

    def fake_build_client(*args, **kwargs):
        client = clients.pop(0)
        built_clients.append(client)
        return client

    monitor = TelegramMonitor()
    monkeypatch.setattr("app.core.monitor.telegram.get_config", lambda: type("Cfg", (), {"monitor": type("M", (), {"telegram": FakeConfig()})()})())
    monkeypatch.setattr("app.core.monitor.telegram.build_telegram_client", fake_build_client)
    monkeypatch.setattr("app.core.monitor.telegram.parse_channels", lambda channels: channels)
    monkeypatch.setattr("app.core.monitor.telegram.TELEGRAM_SESSION_RETRY_DELAY_SECONDS", 0)

    await monitor.start()

    failed_client = built_clients[0]
    assert failed_client.session.raw_connection.closed is True
    assert failed_client.session._conn is None
    assert monitor.client.connected is True
