from datetime import datetime, timezone

import pytest


def test_resolve_requested_range_relative_months():
    from app.services.telegram_history_sync_service import (
        TelegramHistorySyncRequest,
        telegram_history_sync_service,
    )

    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    request = TelegramHistorySyncRequest(
        api_id="1",
        api_hash="2",
        channels=["@demo"],
        mode="relative_range",
        relative_value=6,
        relative_unit="months",
    )

    start, end = telegram_history_sync_service.resolve_requested_range(request, now=now)

    assert end == now
    assert start == datetime(2025, 12, 10, 12, 0, tzinfo=timezone.utc)


def test_resolve_requested_range_date_range():
    from app.services.telegram_history_sync_service import (
        TelegramHistorySyncRequest,
        telegram_history_sync_service,
    )

    request = TelegramHistorySyncRequest(
        api_id="1",
        api_hash="2",
        channels=["@demo"],
        mode="date_range",
        date_start="2026-01-01",
        date_end="2026-01-31",
    )

    start, end = telegram_history_sync_service.resolve_requested_range(request)

    assert start == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert end.year == 2026 and end.month == 1 and end.day == 31
    assert end.tzinfo == timezone.utc


def test_resolve_requested_range_all_requires_channel_bounds():
    from app.services.telegram_history_sync_service import (
        TelegramHistorySyncRequest,
        TelegramValidationError,
        telegram_history_sync_service,
    )

    request = TelegramHistorySyncRequest(
        api_id="1",
        api_hash="2",
        channels=["@demo"],
        mode="all",
    )

    with pytest.raises(TelegramValidationError):
        telegram_history_sync_service.resolve_requested_range(request)


def test_build_chunks_splits_by_chunk_days():
    from app.services.telegram_history_sync_service import telegram_history_sync_service

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, 23, 59, tzinfo=timezone.utc)

    chunks = telegram_history_sync_service.build_chunks("@demo", start, end, chunk_days=3)

    assert len(chunks) == 4
    assert chunks[0].channel_ref == "@demo"
    assert chunks[0].range_start == start.isoformat()
    assert chunks[-1].range_end == end.isoformat()


def test_queue_history_sync_request_returns_success_payload(monkeypatch):
    from app.services.telegram_history_sync_service import (
        TelegramHistorySyncRequest,
        telegram_history_sync_service,
    )

    monkeypatch.setattr(
        "app.services.telegram_history_sync_service.event_bus.emit_background",
        lambda *args, **kwargs: "task-123",
    )

    result = telegram_history_sync_service.queue_history_sync_request(
        TelegramHistorySyncRequest(
            api_id="1",
            api_hash="2",
            channels=["@demo"],
            source="manual",
        )
    )

    assert result["status"] == "success"
    assert result["queued"] is True
    assert result["task_id"] == "task-123"


def test_queue_history_sync_request_deduplicates_active_payload_before_emit(monkeypatch):
    from app.services.telegram_history_sync_service import (
        TelegramHistorySyncRequest,
        TelegramHistorySyncService,
    )

    emitted = []

    def fake_emit_background(*args, **kwargs):
        emitted.append((args, kwargs))
        return f"task-{len(emitted)}"

    monkeypatch.setattr(
        "app.services.telegram_history_sync_service.event_bus.emit_background",
        fake_emit_background,
    )

    service = TelegramHistorySyncService()
    request = TelegramHistorySyncRequest(
        api_id="1",
        api_hash="2",
        channels=["@demo"],
        mode="date_range",
        date_start="2026-01-01",
        date_end="2026-01-31",
        source="manual",
    )

    first = service.queue_history_sync_request(request, name="telegram_history_sync:manual")
    second = service.queue_history_sync_request(request, name="telegram_history_sync:manual")

    assert first["status"] == "success"
    assert second["status"] == "duplicate"
    assert second["task_id"] == first["task_id"]
    assert len(emitted) == 1


def test_monitor_new_link_event_is_no_longer_exported():
    import pytest

    with pytest.raises(ImportError):
        exec("from app.events import EVENT_MONITOR_NEW_LINK", {})
