from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.asyncio
async def test_run_scheduled_sync_queues_history_sync_request(monkeypatch):
    from app.services.telegram_background_service import TelegramBackgroundSyncService

    history_cfg = SimpleNamespace(scheduled_enabled=True, scheduled_interval_minutes=9, emit_new_link_events=False)
    cfg = SimpleNamespace(
        enabled=True,
        api_id="1",
        api_hash="2",
        bot_token="",
        proxy="",
        channels=["@demo"],
        keywords=["电影"],
        history_sync=history_cfg,
    )
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))
    build_request_mock = Mock(return_value=SimpleNamespace(source="scheduled"))
    queue_request_mock = Mock(return_value={"status": "success", "queued": True, "task_id": "task-1"})

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)
    monkeypatch.setattr(
        "app.services.telegram_background_service.telegram_history_sync_service.build_request_for_schedule",
        build_request_mock,
    )
    monkeypatch.setattr(
        "app.services.telegram_background_service.telegram_history_sync_service.queue_history_sync_request",
        queue_request_mock,
    )

    service = TelegramBackgroundSyncService()

    result = await service.run_scheduled_sync()

    assert result["status"] == "success"
    assert result["queued"] is True
    assert result["task_id"] == "task-1"
    build_request_mock.assert_called_once_with()
    queue_request_mock.assert_called_once()


@pytest.mark.asyncio
async def test_run_scheduled_sync_skips_when_disabled(monkeypatch):
    from app.services.telegram_background_service import TelegramBackgroundSyncService

    history_cfg = SimpleNamespace(scheduled_enabled=False, scheduled_interval_minutes=5, emit_new_link_events=False)
    cfg = SimpleNamespace(
        enabled=True,
        api_id="1",
        api_hash="2",
        bot_token="",
        proxy="",
        channels=["@demo"],
        keywords=[],
        history_sync=history_cfg,
    )
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)

    service = TelegramBackgroundSyncService()

    result = await service.run_scheduled_sync()

    assert result == {"status": "skipped", "reason": "telegram_background_sync_disabled"}


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

    cfg = SimpleNamespace(
        enabled=True,
        history_sync=SimpleNamespace(scheduled_enabled=True, scheduled_interval_minutes=7),
    )
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


def test_configure_scheduled_sync_job_disables_when_switch_off(monkeypatch):
    from app.services.telegram_background_service import (
        TELEGRAM_BACKGROUND_SYNC_JOB_ID,
        TelegramBackgroundSyncService,
    )

    cfg = SimpleNamespace(
        enabled=True,
        history_sync=SimpleNamespace(scheduled_enabled=False, scheduled_interval_minutes=7),
    )
    wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=cfg))
    add_job_mock = Mock()
    remove_job_mock = Mock()

    monkeypatch.setattr("app.services.telegram_background_service.get_config", lambda: wrapped_config)
    monkeypatch.setattr("app.services.telegram_background_service.add_job", add_job_mock)
    monkeypatch.setattr("app.services.telegram_background_service.remove_job", remove_job_mock)

    service = TelegramBackgroundSyncService()
    result = service.configure_scheduled_sync_job()

    assert result == {"status": "disabled", "job_id": TELEGRAM_BACKGROUND_SYNC_JOB_ID}
    add_job_mock.assert_not_called()
    remove_job_mock.assert_called_once_with(TELEGRAM_BACKGROUND_SYNC_JOB_ID)