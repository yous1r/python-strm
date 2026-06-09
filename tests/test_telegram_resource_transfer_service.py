from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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
        transfer=SimpleNamespace(enabled=True, temp_dir_id="legacy-temp", archive_dir_id="archive-root"),
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

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.client_115.get_share_info", get_share_info)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.classify", classify_mock)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.client_115.create_path", create_path)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.client_115.share_receive", share_receive)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.generator_115.generate_strm_for_folder", generate_strm)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.generator_115.sync_strm_files_from_manifest", sync_strm)
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
        transfer=SimpleNamespace(enabled=True, temp_dir_id="legacy-temp", archive_dir_id="archive-root"),
        cloud115=SimpleNamespace(target_dir_id="cloud-default"),
        strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
    )

    monkeypatch.setattr("app.services.telegram_resource_transfer_service.get_config", lambda: config)
    monkeypatch.setattr(
        "app.services.telegram_resource_transfer_service.client_115.get_share_info",
        AsyncMock(return_value={"state": True, "files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]}),
    )
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
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.client_115.create_path", create_path)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.client_115.share_receive", share_receive)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.generator_115.generate_strm_for_folder", generate_strm)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service.generator_115.sync_strm_files_from_manifest", sync_strm)
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._update_tg_status", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_success", AsyncMock())
    monkeypatch.setattr("app.services.telegram_resource_transfer_service._notify_transfer_failure", AsyncMock())

    result = await process_resource_transfer(
        {"id": 1, "type": "115", "url": "https://115.com/s/demo", "title": "秘恋稽核中"}
    )

    assert result["status"] == "success"
    create_path.assert_awaited_once_with(
        "archive-root",
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