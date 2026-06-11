from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router


@pytest.mark.asyncio
async def test_get_monitor_status_returns_channel_states(monkeypatch):
    from app.services.telegram_service import get_monitor_status

    async def fake_list_states():
        return [{"channel_ref": "@demo", "last_message_id": 7, "last_error": ""}]

    monkeypatch.setattr("app.services.telegram_service.list_telegram_monitor_states", fake_list_states)
    monkeypatch.setattr("app.services.telegram_service.telegram_monitor", type("M", (), {"client": None})())

    status = await get_monitor_status()
    assert status["enabled"] in (True, False)
    assert status["channels"][0]["channel_ref"] == "@demo"


@pytest.mark.asyncio
async def test_sync_single_channel_uses_last_message_state(monkeypatch):
    from app.services.telegram_service import sync_single_channel

    calls = []

    async def fake_sync_channel_history(client, channel_ref, **kwargs):
        calls.append((channel_ref, kwargs["limit"]))
        return {"processed": 3, "inserted": 2}

    async def fake_state(channel_ref):
        return {"channel_ref": channel_ref, "last_message_id": 88}

    monkeypatch.setattr("app.services.telegram_service._sync_channel_history", fake_sync_channel_history)
    monkeypatch.setattr("app.services.telegram_service.get_telegram_monitor_state", fake_state)
    monkeypatch.setattr(
        "app.services.telegram_service._acquire_client",
        AsyncMock(return_value=(type("C", (), {"disconnect": AsyncMock()})(), False, True, None)),
    )

    result = await sync_single_channel("1", "2", channels=["@demo"], channel_ref="@demo")
    assert result["processed"] == 3
    assert calls == [("@demo", 100)]


@pytest.mark.asyncio
async def test_scrape_monitor_history_filters_keywords_locally(monkeypatch):
    from app.services.telegram_service import scrape_monitor_history

    class FakeMessage:
        def __init__(self, message_id, text):
            self.id = message_id
            self.message = text
            self.text = text
            self.date = None
            self.chat_id = -1001234567890

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.messages = [
                FakeMessage(1, "无关内容"),
                FakeMessage(2, "电影资源 https://115.com/s/abc"),
            ]

        async def iter_messages(self, channel, limit=None, search=None):
            self.calls.append({"channel": channel, "limit": limit, "search": search})
            for message in self.messages:
                yield message

        async def disconnect(self):
            return None

    dispatched = []
    client = FakeClient()

    async def fake_acquire_client(*args, **kwargs):
        return client, True, True, None

    async def fake_dispatch(channel, message, *, emit_events=True):
        dispatched.append((channel, message.id))
        return [{"db_id": 2}]

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.telegram_service._acquire_client", fake_acquire_client)
    monkeypatch.setattr("app.services.telegram_service._dispatch_scraped_message", fake_dispatch)
    monkeypatch.setattr("app.services.telegram_service._throttle_scrape", no_sleep)

    await scrape_monitor_history("1", "2", channels=["@demo"], keywords=["电影", "资源"])

    assert client.calls == [{"channel": "demo", "limit": None, "search": None}]
    assert dispatched == [("demo", 2)]


@pytest.mark.asyncio
async def test_dispatch_scraped_message_prefers_message_chat_id(monkeypatch):
    from app.services.telegram_service import _dispatch_scraped_message

    captured = {}

    class FakeMessage:
        id = 9
        message = "电影资源 https://115.com/s/abc"
        text = message
        date = None
        chat_id = -1009876543210

    async def fake_ingest_message(text, message_id=None, channel_id=None, msg_date=None, torrent_files=None):
        captured["text"] = text
        captured["message_id"] = message_id
        captured["channel_id"] = channel_id
        captured["msg_date"] = msg_date
        captured["torrent_files"] = torrent_files
        return []

    monkeypatch.setattr("app.services.telegram_service.telegram_monitor.ingest_message", fake_ingest_message)

    resources = await _dispatch_scraped_message("demo", FakeMessage())

    assert resources == []
    assert captured["message_id"] == 9
    assert captured["channel_id"] == "-1009876543210"
    assert captured["torrent_files"] == []


@pytest.mark.asyncio
async def test_sync_configured_channels_collects_resources(monkeypatch):
    from app.services.telegram_service import sync_configured_channels

    async def fake_sync_single_channel(*args, **kwargs):
        return {
            "channel_ref": kwargs["channel_ref"],
            "processed": 1,
            "inserted": 1,
            "resources": [{"db_id": 1, "url": "https://115.com/s/abc", "type": "115"}],
        }

    monkeypatch.setattr("app.services.telegram_service.sync_single_channel", fake_sync_single_channel)

    result = await sync_configured_channels("1", "2", channels=["@a", "@b"])

    assert len(result["channels"]) == 2
    assert len(result["resources"]) == 2


@pytest.mark.asyncio
async def test_sync_single_channel_rebuilds_when_state_exists_but_resources_missing(monkeypatch):
    from app.services.telegram_service import _sync_channel_history

    class FakeMessage:
        def __init__(self, message_id, text):
            self.id = message_id
            self.text = text
            self.message = text
            self.date = None

    class FakeClient:
        async def iter_messages(self, channel, limit=None):
            yield FakeMessage(100, "电影资源 https://115.com/s/abc")
            yield FakeMessage(99, "旧资源 https://115.com/s/def")

    dispatched = []
    saved_states = []

    async def fake_state(channel_ref):
        return {"channel_ref": channel_ref, "last_message_id": 100, "last_message_date": "2026-06-06T10:00:00"}

    async def fake_dispatch(channel, message, *, emit_events=True):
        dispatched.append((channel, message.id))
        return [{"db_id": 2}]

    async def fake_upsert(**kwargs):
        saved_states.append(kwargs)

    async def fake_has_resources(channel_ref, channel_id):
        return False

    monkeypatch.setattr("app.services.telegram_service.get_telegram_monitor_state", fake_state)
    monkeypatch.setattr("app.services.telegram_service._dispatch_scraped_message", fake_dispatch)
    monkeypatch.setattr("app.services.telegram_service.upsert_telegram_monitor_state", fake_upsert)
    monkeypatch.setattr("app.services.telegram_service._channel_has_resources", fake_has_resources)

    result = await _sync_channel_history(FakeClient(), "@demo", emit_events=True, startup_mode="incremental", limit=10)

    assert dispatched == [("demo", 100), ("demo", 99)]
    assert result["inserted"] == 2
    assert saved_states[0]["last_message_id"] == 100


@pytest.mark.asyncio
async def test_sync_channel_history_does_not_stop_after_first_message_when_no_state(monkeypatch):
    from app.services.telegram_service import _sync_channel_history

    class FakeMessage:
        def __init__(self, message_id, text):
            self.id = message_id
            self.text = text
            self.message = text
            self.date = None

    class FakeClient:
        async def iter_messages(self, channel, limit=None):
            yield FakeMessage(100, "纯文本公告")
            yield FakeMessage(99, "电影资源 https://115.com/s/abc")

    dispatched = []
    saved_states = []

    async def fake_state(channel_ref):
        return None

    async def fake_dispatch(channel, message, *, emit_events=True):
        dispatched.append((channel, message.id))
        if message.id == 99:
            return [{"db_id": 2}]
        return []

    async def fake_upsert(**kwargs):
        saved_states.append(kwargs)

    async def fake_has_resources(channel_ref, channel_id):
        return False

    monkeypatch.setattr("app.services.telegram_service.get_telegram_monitor_state", fake_state)
    monkeypatch.setattr("app.services.telegram_service._dispatch_scraped_message", fake_dispatch)
    monkeypatch.setattr("app.services.telegram_service.upsert_telegram_monitor_state", fake_upsert)
    monkeypatch.setattr("app.services.telegram_service._channel_has_resources", fake_has_resources)

    result = await _sync_channel_history(FakeClient(), "@demo", emit_events=False, startup_mode="incremental", limit=10)

    assert dispatched == [("demo", 100), ("demo", 99)]
    assert result["inserted"] == 1
    assert [resource["db_id"] for resource in result["resources"]] == [2]
    assert saved_states[0]["last_message_id"] == 100


def test_get_telegram_status_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_status():
        return {"enabled": True, "running": False, "channels": []}

    monkeypatch.setattr("app.api.system.get_monitor_status", fake_status)
    client = TestClient(app)
    response = client.get("/system/telegram/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is True