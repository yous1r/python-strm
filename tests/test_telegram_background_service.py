from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_run_scheduled_sync_triggers_followup_full_sync_when_transfer_succeeds(monkeypatch):
    from app.services.telegram_background_service import TelegramBackgroundSyncService

    cfg = SimpleNamespace(
        enabled=True,
        scheduled_sync_enabled=True,
        api_id="1",
        api_hash="2",
        bot_token="",
        proxy="",
        channels=["@demo"],
        keywords=["电影"],
        scheduled_sync_limit=20,
        full_sync_skip_if_scheduled_within_minutes=20,
    )
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))

    sync_mock = AsyncMock(return_value={
        "status": "success",
        "channels": [{"channel_ref": "@demo", "resources": [{"db_id": 1, "type": "115", "url": "https://115.com/s/abc"}]}],
        "resources": [{"db_id": 1, "type": "115", "url": "https://115.com/s/abc"}],
    })
    transfer_mock = AsyncMock(return_value={"status": "success"})
    full_sync_mock = AsyncMock(return_value={"status": "success", "task_id": "fs-1"})

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)
    monkeypatch.setattr("app.services.telegram_background_service.sync_configured_channels", sync_mock)
    monkeypatch.setattr("app.services.telegram_background_service.process_resource_transfer", transfer_mock)
    monkeypatch.setattr(
        "app.services.telegram_background_service.cloud115_full_sync_service.start_full_sync",
        full_sync_mock,
    )

    service = TelegramBackgroundSyncService()
    monkeypatch.setattr(service, "is_full_sync_scheduled_within", lambda minutes: False)

    result = await service.run_scheduled_sync()

    assert result["successful_transfers"] == 1
    assert result["full_sync"]["status"] == "success"
    full_sync_mock.assert_awaited_once_with(source="telegram_monitor")


@pytest.mark.asyncio
async def test_run_scheduled_sync_skips_followup_when_db_sync_is_imminent(monkeypatch):
    from app.services.telegram_background_service import TelegramBackgroundSyncService

    cfg = SimpleNamespace(
        enabled=True,
        scheduled_sync_enabled=True,
        api_id="1",
        api_hash="2",
        bot_token="",
        proxy="",
        channels=["@demo"],
        keywords=[],
        scheduled_sync_limit=20,
        full_sync_skip_if_scheduled_within_minutes=20,
    )
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)
    monkeypatch.setattr(
        "app.services.telegram_background_service.sync_configured_channels",
        AsyncMock(return_value={"status": "success", "channels": [], "resources": [{"db_id": 1, "type": "115", "url": "https://115.com/s/abc"}]}),
    )
    monkeypatch.setattr(
        "app.services.telegram_background_service.process_resource_transfer",
        AsyncMock(return_value={"status": "success"}),
    )
    full_sync_mock = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(
        "app.services.telegram_background_service.cloud115_full_sync_service.start_full_sync",
        full_sync_mock,
    )

    service = TelegramBackgroundSyncService()
    monkeypatch.setattr(service, "is_full_sync_scheduled_within", lambda minutes: True)

    result = await service.run_scheduled_sync()

    assert result["full_sync"]["status"] == "skipped"
    assert result["full_sync"]["reason"] == "db_sync_already_scheduled_soon"
    full_sync_mock.assert_not_awaited()


def test_is_full_sync_scheduled_within_uses_future_window(monkeypatch):
    from app.services.telegram_background_service import TelegramBackgroundSyncService

    next_run_time = datetime.now() + timedelta(minutes=10)
    monkeypatch.setattr(
        "app.services.telegram_background_service.get_job",
        lambda job_id: SimpleNamespace(next_run_time=next_run_time),
    )

    service = TelegramBackgroundSyncService()

    assert service.is_full_sync_scheduled_within(20) is True
    assert service.is_full_sync_scheduled_within(5) is False


def test_configure_scheduled_sync_job_registers_interval(monkeypatch):
    from app.services.telegram_background_service import (
        TELEGRAM_BACKGROUND_SYNC_JOB_ID,
        TelegramBackgroundSyncService,
    )

    cfg = SimpleNamespace(enabled=True, scheduled_sync_enabled=True, scheduled_sync_interval_minutes=7)
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))
    add_job_mock = Mock()
    remove_job_mock = Mock()

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)
    monkeypatch.setattr("app.services.telegram_background_service.add_job", add_job_mock)
    monkeypatch.setattr("app.services.telegram_background_service.remove_job", remove_job_mock)

    service = TelegramBackgroundSyncService()
    result = service.configure_scheduled_sync_job()

    assert result["status"] == "enabled"
    assert result["job_id"] == TELEGRAM_BACKGROUND_SYNC_JOB_ID
    add_job_mock.assert_called_once()
    remove_job_mock.assert_not_called()