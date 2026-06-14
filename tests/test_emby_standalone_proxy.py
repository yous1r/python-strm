import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.config import EmbyProxyInstanceConfig, EmbyStrmPathMappingConfig
from app.core.emby.standalone_proxy import (
    _cache_media_item_playback_record,
    _extract_115_pickcode,
    _get_playback_record_by_media_item,
    _preheat_instance_media_item_links,
    _resolve_playback_url,
    _resolve_local_strm_path,
)


class ResolveLocalStrmPathTests(unittest.TestCase):
    def test_resolves_configured_emby_prefix_to_local_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir) / "archive"
            strm_file = local_root / "综艺" / "国产综艺" / "现在就出发 (2023) {tmdb-231620}" / "Season 1" / "现在就出发 - S01E01-Part1 - 第 1 集.strm"
            strm_file.parent.mkdir(parents=True)
            strm_file.write_text("http://localhost/api/v1/115/play/pickcode", encoding="utf-8")

            instance = EmbyProxyInstanceConfig(
                strm_path_mappings=[
                    EmbyStrmPathMappingConfig(
                        emby_prefix="/mnt/strm-self/archive",
                        local_prefix=str(local_root),
                    )
                ]
            )

            resolved = _resolve_local_strm_path(
                "/mnt/strm-self/archive/综艺/国产综艺/现在就出发 (2023) {tmdb-231620}/Season 1/现在就出发 - S01E01-Part1 - 第 1 集.strm",
                instance,
            )

        self.assertEqual(resolved, str(strm_file))


class PlaybackIndexTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_pickcode_from_project_strm_url(self):
        pickcode = _extract_115_pickcode("http://localhost:8095/api/v1/115/play/abc123/video.mkv|User-Agent=test")

        self.assertEqual(pickcode, "abc123")

    async def test_loads_playback_record_from_media_item_link(self):
        instance = EmbyProxyInstanceConfig(name="fnos")
        link = {
            "file_id": "fid-1",
            "play_identity": "pickcode-1",
            "strm_record_id": 5,
            "source_path": "/mnt/archive/show.strm",
        }

        with patch("app.core.emby.standalone_proxy.get_media_item_link", AsyncMock(return_value=link)) as mocked:
            record = await _get_playback_record_by_media_item("item-1", "source-1", instance)

        self.assertEqual(record["id"], 5)
        self.assertEqual(record["file_id"], "fid-1")
        self.assertEqual(record["play_identity"], "pickcode-1")
        mocked.assert_awaited_once_with(
            media_server_type="emby",
            media_server_name="fnos",
            media_item_id="item-1",
            media_source_id="source-1",
        )

    async def test_caches_media_item_link_with_generic_identity(self):
        instance = EmbyProxyInstanceConfig(name="fnos")
        record = {
            "id": 7,
            "file_id": "fid-7",
            "play_identity": "pickcode-7",
            "strm_path": "/mnt/archive/show.strm",
        }

        with patch("app.core.emby.standalone_proxy.upsert_media_item_link", AsyncMock()) as mocked:
            await _cache_media_item_playback_record(
                item_id="item-7",
                media_source_id="source-7",
                record=record,
                instance=instance,
            )

        mocked.assert_awaited_once_with(
            media_server_type="emby",
            media_server_name="fnos",
            media_item_id="item-7",
            media_source_id="source-7",
            cloud_type="115",
            file_id="fid-7",
            play_identity="pickcode-7",
            strm_record_id=7,
            source_path="/mnt/archive/show.strm",
        )

    async def test_preheat_links_media_items_from_direct_pickcode(self):
        instance = EmbyProxyInstanceConfig(name="fnos", url="http://emby.local", api_key="token")
        items = [
            {
                "Id": "item-1",
                "Path": "http://localhost:8095/api/v1/115/play/pickcode-1/video.mkv",
                "MediaSources": [{"Id": "source-1"}],
            }
        ]

        with (
            patch("app.core.emby.standalone_proxy._iterate_upstream_items", AsyncMock(return_value=items)),
            patch("app.core.emby.standalone_proxy._get_playback_record_by_media_item", AsyncMock(return_value=None)),
            patch("app.core.emby.standalone_proxy._cache_media_item_playback_record", AsyncMock()) as mocked_cache,
        ):
            result = await _preheat_instance_media_item_links(instance)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["linked"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["failed"], 0)
        mocked_cache.assert_awaited_once()

    async def test_preheat_skips_existing_link_when_not_overwrite(self):
        instance = EmbyProxyInstanceConfig(name="fnos", url="http://emby.local", api_key="token")
        items = [
            {
                "Id": "item-1",
                "Path": "http://localhost:8095/api/v1/115/play/pickcode-1/video.mkv",
                "MediaSources": [{"Id": "source-1"}],
            }
        ]

        with (
            patch("app.core.emby.standalone_proxy._iterate_upstream_items", AsyncMock(return_value=items)),
            patch("app.core.emby.standalone_proxy._get_playback_record_by_media_item", AsyncMock(return_value={"play_identity": "existing"})),
            patch("app.core.emby.standalone_proxy._cache_media_item_playback_record", AsyncMock()) as mocked_cache,
        ):
            result = await _preheat_instance_media_item_links(instance, overwrite=False)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["linked"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        mocked_cache.assert_not_awaited()

    async def test_preheat_falls_back_to_local_path_lookup(self):
        instance = EmbyProxyInstanceConfig(name="fnos", url="http://emby.local", api_key="token")
        items = [
            {
                "Id": "item-2",
                "Path": "/mnt/strm-self/archive/show.strm",
                "MediaSources": [{"Id": "source-2"}],
            }
        ]
        record = {
            "id": 9,
            "file_id": "fid-9",
            "play_identity": "pickcode-9",
            "strm_path": "/mnt/strm-self/archive/show.strm",
        }

        with (
            patch("app.core.emby.standalone_proxy._iterate_upstream_items", AsyncMock(return_value=items)),
            patch("app.core.emby.standalone_proxy._get_playback_record_by_media_item", AsyncMock(return_value=None)),
            patch("app.core.emby.standalone_proxy._get_local_playback_record_by_path", AsyncMock(return_value=record)) as mocked_lookup,
            patch("app.core.emby.standalone_proxy._cache_media_item_playback_record", AsyncMock()) as mocked_cache,
        ):
            result = await _preheat_instance_media_item_links(instance)

        self.assertEqual(result["linked"], 1)
        mocked_lookup.assert_awaited_once_with("/mnt/strm-self/archive/show.strm", instance)
        mocked_cache.assert_awaited_once()

    async def test_resolve_playback_url_falls_back_to_local_path_lookup(self):
        instance = EmbyProxyInstanceConfig(name="fnos", url="http://emby.local", api_key="token")
        request = SimpleNamespace(
            headers={"user-agent": "VidHub"},
            query_params={"MediaSourceId": "source-3"},
        )
        record = {
            "id": 11,
            "file_id": "fid-11",
            "play_identity": "pickcode-11",
            "strm_path": "/mnt/strm-self/archive/show.strm",
        }
        mocked_download_url = AsyncMock(return_value="https://cdn.example/video.mkv")
        plugin = SimpleNamespace(client=SimpleNamespace(get_download_url=mocked_download_url))

        with (
            patch("app.core.emby.standalone_proxy._get_playback_record_by_media_item", AsyncMock(return_value=None)) as mocked_link,
            patch(
                "app.core.emby.standalone_proxy._get_upstream_item_payload",
                AsyncMock(return_value={
                    "Path": "/mnt/strm-self/archive/show.strm",
                    "MediaSources": [{"Id": "source-3"}],
                }),
            ),
            patch("app.core.emby.standalone_proxy._get_local_playback_record_by_path", AsyncMock(return_value=record)) as mocked_lookup,
            patch("app.core.emby.standalone_proxy._cache_media_item_playback_record", AsyncMock()) as mocked_cache,
            patch("app.core.emby.standalone_proxy.get_cloud_plugin", return_value=plugin),
        ):
            resolved = await _resolve_playback_url("http://emby.local", "token", "item-3", request, instance)

        self.assertEqual(resolved, "https://cdn.example/video.mkv")
        mocked_link.assert_awaited_once_with("item-3", media_source_id="source-3", instance=instance)
        mocked_lookup.assert_awaited_once_with("/mnt/strm-self/archive/show.strm", instance)
        mocked_cache.assert_awaited_once()
        mocked_download_url.assert_awaited_once_with("pickcode-11", user_agent="VidHub")

    def test_uses_longest_matching_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            broad_root = Path(temp_dir) / "broad"
            archive_root = Path(temp_dir) / "archive"
            strm_file = archive_root / "show.strm"
            strm_file.parent.mkdir(parents=True)
            strm_file.write_text("content", encoding="utf-8")

            instance = EmbyProxyInstanceConfig(
                strm_path_mappings=[
                    EmbyStrmPathMappingConfig(emby_prefix="/mnt/strm-self", local_prefix=str(broad_root)),
                    EmbyStrmPathMappingConfig(emby_prefix="/mnt/strm-self/archive", local_prefix=str(archive_root)),
                ]
            )

            resolved = _resolve_local_strm_path("/mnt/strm-self/archive/show.strm", instance)

        self.assertEqual(resolved, str(strm_file))

    def test_keeps_legacy_strm_output_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(temp_dir)
                strm_file = Path("strm_output") / "剧集" / "episode.strm"
                strm_file.parent.mkdir(parents=True)
                strm_file.write_text("content", encoding="utf-8")

                resolved = _resolve_local_strm_path("/vol1/docker-data/python-strm/strm_output/剧集/episode.strm")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(resolved, str(strm_file))


if __name__ == "__main__":
    unittest.main()
