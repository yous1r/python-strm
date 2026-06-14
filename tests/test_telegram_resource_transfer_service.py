from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import pytest

from app.services.telegram_resource_transfer_service import normalize_resource_payload


def _cloud_plugin(client, generator):
    return SimpleNamespace(client=client, strm_generator=generator)


def test_normalize_resource_payload_filters_promo_urls_and_falls_back_to_real_share_link():
    payload = normalize_resource_payload(
        {
            "id": 7,
            "link": "https://tgstat.ru/channel/@demo",
            "disk_type": "url",
            "password": "",
            "resource_links": [
                {"url": "https://tgstat.ru/channel/@demo", "raw_url": "https://tgstat.ru/channel/@demo", "type": "url", "password": ""},
                {"url": "https://115.com/s/abc", "raw_url": "https://115.com/s/abc?password=9x8y", "type": "115", "password": "9x8y"},
                {"url": "https://example.com/jump", "raw_url": "https://example.com/jump", "type": "url", "password": ""},
            ],
            "magnet_links": ["magnet:?xt=urn:btih:FACEB00C"],
        }
    )

    assert payload["type"] == "115"
    assert payload["disk_type"] == "115"
    assert payload["url"] == "https://115.com/s/abc"
    assert payload["password"] == "9x8y"
    assert payload["url_links"] == ["https://115.com/s/abc?password=9x8y"]
    assert payload["resource_count"] == 1


def test_normalize_resource_payload_clears_invalid_plain_url_when_no_real_resource_exists():
    payload = normalize_resource_payload(
        {
            "id": 8,
            "link": "https://example.com/promo",
            "disk_type": "url",
            "password": "abcd",
            "resource_links": [{"url": "https://example.com/promo", "raw_url": "https://example.com/promo", "type": "url", "password": "abcd"}],
        }
    )

    assert payload["type"] == "url"
    assert payload["url"] == ""
    assert payload["link"] == ""
    assert payload["password"] == ""
    assert payload["resource_links"] == []
    assert payload["resource_count"] == 0


@pytest.mark.asyncio
async def test_process_resource_transfer_uses_series_folder_id_without_falling_back_to_temp_dir(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_resource_transfer

    config = SimpleNamespace(
        monitor=SimpleNamespace(
            telegram=SimpleNamespace(
                archive_dir_id="monitor-archive-ignored",
                target_dir_id="monitor-target-ignored",
                filter_rules=[],
                auto_organize=False,
            )
        ),
        transfer=SimpleNamespace(enabled=True),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )

    get_share_info = AsyncMock(return_value={"state": True, "files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]})
    classify_mock = AsyncMock(return_value=SimpleNamespace(
        category="剧集",
        subcategory="日韩剧集",
        title="秘恋稽核中",
        year="2026",
        tmdb_id="297640",
        season=1,
        media_type="tv",
    ))
    create_path = AsyncMock(return_value={"id": "cid-123"})
    share_receive = AsyncMock(return_value={"state": True, "share_files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]})
    generate_strm = AsyncMock(return_value=["strm_output/a.strm"])
    sync_strm = AsyncMock(return_value={"scanned": 3, "updated": 2, "skipped": 1, "failed": 0})
    client = SimpleNamespace(
        client=object(),
        get_share_info=get_share_info,
        create_path=create_path,
        share_receive=share_receive,
    )
    generator = SimpleNamespace(
        generate_strm_for_folder=generate_strm,
        sync_strm_files_from_manifest=sync_strm,
    )

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_cloud_plugin", lambda cloud_type: _cloud_plugin(client, generator))
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.classify", classify_mock)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", AsyncMock())

    result = await process_resource_transfer(
        {"id": 1, "type": "115", "url": "https://115.com/s/demo", "series_folder_id": "series-final"}
    )

    assert result["status"] == "success"
    create_path.assert_not_awaited()
    share_receive.assert_awaited_once_with(
        "https://115.com/s/demo",
        "",
        "series-final",
        filter_rules=[],
    )
    generate_strm.assert_awaited_once()
    sync_strm.assert_awaited_once_with(
        dir_id="series-final",
        output_dir="strm_output",
        root_output_dir="strm_output",
        base_url="http://localhost:8095",
        archive_root="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}",
    )


@pytest.mark.asyncio
async def test_process_resource_transfer_precreates_archive_dir_and_ignores_temp_dir_fallback(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_resource_transfer

    config = SimpleNamespace(
        monitor=SimpleNamespace(
            telegram=SimpleNamespace(
                archive_dir_id="monitor-archive-ignored",
                target_dir_id="monitor-target-ignored",
                filter_rules=[],
                auto_organize=False,
            )
        ),
        transfer=SimpleNamespace(enabled=True),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr(
        "app.services.telegram_resource_transfer_service.classify",
        AsyncMock(return_value=SimpleNamespace(
            category="剧集",
            subcategory="日韩剧集",
            title="秘恋稽核中",
            year="2026",
            tmdb_id="297640",
            season=1,
            media_type="tv",
        )),
    )
    create_path = AsyncMock(return_value={"id": "cid-123"})
    share_receive = AsyncMock(return_value={"state": True, "share_files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]})
    generate_strm = AsyncMock(return_value=["strm_output/a.strm"])
    sync_strm = AsyncMock(return_value={"scanned": 3, "updated": 2, "skipped": 1, "failed": 0})
    client = SimpleNamespace(
        client=object(),
        get_share_info=AsyncMock(return_value={"state": True, "files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]}),
        create_path=create_path,
        share_receive=share_receive,
    )
    generator = SimpleNamespace(
        generate_strm_for_folder=generate_strm,
        sync_strm_files_from_manifest=sync_strm,
    )
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_cloud_plugin", lambda cloud_type: _cloud_plugin(client, generator))
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", AsyncMock())

    result = await process_resource_transfer(
        {"id": 1, "type": "115", "url": "https://115.com/s/demo", "title": "秘恋稽核中", "target_dir_id": "strm-root"}
    )

    assert result["status"] == "success"
    create_path.assert_awaited_once_with(
        "strm-root",
        "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
    )
    share_receive.assert_awaited_once_with(
        "https://115.com/s/demo",
        "",
        "cid-123",
        filter_rules=[],
    )
    generate_strm.assert_awaited_once()
    sync_strm.assert_awaited_once_with(
        dir_id="cid-123",
        output_dir="strm_output",
        root_output_dir="strm_output",
        base_url="http://localhost:8095",
        archive_root="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}",
    )


@pytest.mark.asyncio
async def test_process_resource_transfer_does_not_hold_transfer_slot_during_cooldown(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_resource_transfer

    config = SimpleNamespace(
        monitor=SimpleNamespace(
            telegram=SimpleNamespace(
                filter_rules=[],
                transfer_concurrency=1,
                transfer_cooldown_seconds=5,
            )
        ),
        transfer=SimpleNamespace(enabled=True),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )
    share_calls = 0
    first_share_done = asyncio.Event()
    second_share_started = asyncio.Event()
    cooldown_started = asyncio.Event()
    release_cooldown = asyncio.Event()

    async def fake_share_receive(*args, **kwargs):
        nonlocal share_calls
        share_calls += 1
        if share_calls == 1:
            first_share_done.set()
        if share_calls == 2:
            second_share_started.set()
        return {"state": True, "share_files": []}

    async def fake_sleep(seconds):
        if seconds == 5:
            cooldown_started.set()
            await release_cooldown.wait()

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    client = SimpleNamespace(client=object(), share_receive=fake_share_receive)
    generator = SimpleNamespace(generate_strm_for_folder=AsyncMock(), sync_strm_files_from_manifest=AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_cloud_plugin", lambda cloud_type: _cloud_plugin(client, generator))
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", AsyncMock())

    first = asyncio.create_task(process_resource_transfer({"id": 1, "type": "115", "url": "https://115.com/s/a", "series_folder_id": "series-a"}))
    await asyncio.wait_for(first_share_done.wait(), timeout=1)
    await asyncio.wait_for(cooldown_started.wait(), timeout=1)

    second = asyncio.create_task(process_resource_transfer({"id": 2, "type": "115", "url": "https://115.com/s/b", "series_folder_id": "series-b"}))
    try:
        await asyncio.wait_for(second_share_started.wait(), timeout=0.05)
    finally:
        release_cooldown.set()
        await asyncio.gather(first, second)

    assert share_calls == 2


@pytest.mark.asyncio
async def test_process_resource_transfer_does_not_notify_failure_when_filter_rules_do_not_match(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_resource_transfer

    config = SimpleNamespace(
        monitor=SimpleNamespace(
            telegram=SimpleNamespace(
                filter_rules=["S01E02"],
                transfer_concurrency=2,
                transfer_cooldown_seconds=0,
            )
        ),
        transfer=SimpleNamespace(enabled=True),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )
    share_receive = AsyncMock(return_value={
        "state": False,
        "error": "No files found in share or none matched filter rules",
    })
    client = SimpleNamespace(client=object(), share_receive=share_receive)
    generator = SimpleNamespace(generate_strm_for_folder=AsyncMock(), sync_strm_files_from_manifest=AsyncMock())
    notify_failure = AsyncMock()
    update_status = AsyncMock()

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_cloud_plugin", lambda cloud_type: _cloud_plugin(client, generator))
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", update_status)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", notify_failure)

    result = await process_resource_transfer({
        "id": 9,
        "type": "115",
        "url": "https://115.com/s/abc123",
        "series_folder_id": "series-final",
    })

    assert result["status"] == "skipped"
    assert result["reason"] == "no_matching_filter_rules"
    notify_failure.assert_not_awaited()
    update_status.assert_any_await(9, "queued")
    update_status.assert_any_await(9, "skipped")


@pytest.mark.asyncio
async def test_process_resource_transfer_precreates_from_selected_strm_destination_without_legacy_archive(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_resource_transfer

    config = SimpleNamespace(
        monitor=SimpleNamespace(
            telegram=SimpleNamespace(
                archive_dir_id="monitor-archive-ignored",
                target_dir_id="monitor-target-ignored",
                filter_rules=[],
                auto_organize=False,
            )
        ),
        transfer=SimpleNamespace(enabled=True),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr(
        "app.services.telegram_resource_transfer_service.classify",
        AsyncMock(return_value=SimpleNamespace(
            category="剧集",
            subcategory="日韩剧集",
            title="秘恋稽核中",
            year="2026",
            tmdb_id="297640",
            season=1,
            media_type="tv",
        )),
    )
    create_path = AsyncMock(return_value={"id": "cid-123"})
    share_receive = AsyncMock(return_value={"state": True, "share_files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]})
    generate_strm = AsyncMock(return_value=["strm_output/a.strm"])
    sync_strm = AsyncMock(return_value={"scanned": 3, "updated": 2, "skipped": 1, "failed": 0})
    client = SimpleNamespace(
        client=object(),
        get_share_info=AsyncMock(return_value={"state": True, "files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]}),
        create_path=create_path,
        share_receive=share_receive,
    )
    generator = SimpleNamespace(
        generate_strm_for_folder=generate_strm,
        sync_strm_files_from_manifest=sync_strm,
    )
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_cloud_plugin", lambda cloud_type: _cloud_plugin(client, generator))
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", AsyncMock())

    result = await process_resource_transfer(
        {
            "id": 1,
            "type": "115",
            "url": "https://115.com/s/demo",
            "title": "秘恋稽核中",
            "target_dir_id": "strm-root",
        }
    )

    assert result["status"] == "success"
    create_path.assert_awaited_once_with(
        "strm-root",
        "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
    )
    share_receive.assert_awaited_once_with(
        "https://115.com/s/demo",
        "",
        "cid-123",
        filter_rules=[],
    )
    sync_strm.assert_awaited_once_with(
        dir_id="cid-123",
        output_dir="strm_output",
        root_output_dir="strm_output",
        base_url="http://localhost:8095",
        archive_root="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}",
    )
