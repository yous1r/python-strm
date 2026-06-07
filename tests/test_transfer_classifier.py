import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace

from app.core.cloud115.client import Cloud115Client
from app.core.media.parser import parse_filename
from app.core.transfer.classifier import _sanitize, classify
from app.core.transfer.batch import (
    _batch_states,
    _build_batch_sample_name,
    handle_batch_done,
    handle_batch_item_done,
    handle_batch_requested,
)
from app.core.cloud115.strm import StrmGenerator115
from app.core.media.organizer import MediaOrganizer


class ParseFilenameTests(unittest.TestCase):
    def test_parse_filename_detects_episode_without_video_extension(self):
        info = parse_filename("📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP (2026)")

        self.assertEqual(info.media_type, "episode")
        self.assertEqual(info.season, 1)
        self.assertEqual(info.episode, 1)


class ClassifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_treats_extensionless_episode_title_as_tv(self):
        tmdb_data = {
            "id": 297640,
            "name": "秘恋稽核中",
            "first_air_date": "2026-01-01",
        }

        with patch(
            "app.core.transfer.classifier.media_organizer.get_organized_path",
            return_value=("剧集", "日韩", "秘恋稽核中 (2026)/Season 01", "秘恋稽核中 - S01E01", tmdb_data),
        ):
            result = await classify("📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP (2026)")

        self.assertIsNotNone(result)
        self.assertEqual(result.category, "剧集")
        self.assertEqual(result.subcategory, "日韩剧集")
        self.assertEqual(result.media_type, "tv")
        self.assertEqual(result.season, 1)
        self.assertEqual(result.title, "秘恋稽核中")
        self.assertEqual(result.year, "2026")

    def test_sanitize_removes_display_prefix_from_fallback_title(self):
        self.assertEqual(_sanitize("📺 大唐迷雾 (2026)"), "大唐迷雾 (2026)")


class BatchPrepareTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        _batch_states.clear()

    def test_build_batch_sample_name_prefers_base_title_and_preserves_episode_hint(self):
        sample_name = _build_batch_sample_name(
            "秘恋稽核中",
            "📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP (2026)",
        )

        self.assertEqual(sample_name, "秘恋稽核中 (2026) S01E01")

    async def test_handle_batch_requested_precreates_archive_path_from_base_title_sample(self):
        rows = [{
            "id": 1,
            "title": "📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP (2026)",
            "base_title": "秘恋稽核中",
            "link": "https://115.com/s/demo",
            "password": "",
        }]
        classify_result = SimpleNamespace(
            category="剧集",
            subcategory="日韩剧集",
            title="秘恋稽核中",
            year="2026",
            tmdb_id="297640",
            season=1,
            media_type="tv",
        )
        mocked_emit = AsyncMock()
        config = SimpleNamespace(
            transfer=SimpleNamespace(archive_dir_id="archive-root"),
        )

        with patch("app.core.transfer.batch.classify", AsyncMock(return_value=classify_result)) as mocked_classify, patch(
            "app.core.transfer.batch.client_115.create_path",
            AsyncMock(return_value={"id": "cid-123"}),
        ) as mocked_create_path, patch(
            "app.core.transfer.batch.get_config",
            return_value=config,
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            mocked_emit,
        ):
            await handle_batch_requested("task-1", rows, base_title="秘恋稽核中")

        mocked_classify.assert_awaited_once_with("秘恋稽核中 (2026) S01E01")
        mocked_create_path.assert_awaited_once_with(
            "archive-root",
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
        )
        self.assertEqual(_batch_states["task-1"]["series_path_str"], "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1")
        mocked_emit.assert_awaited_once()

    async def test_handle_batch_item_done_accumulates_share_files(self):
        _batch_states["task-2"] = {
            "task_id": "task-2",
            "title": "秘恋稽核中",
            "episode_count": 2,
            "series_folder_id": "cid-1",
            "series_path_str": "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            "share_files": [{"sha": "A1", "name": "E01.mkv"}],
            "success_count": 0,
            "failed_links": [],
            "group": "transfer-batch:秘恋稽核中",
        }

        with patch("app.core.transfer.batch._update_tg_status", AsyncMock()) as mocked_update, patch(
            "app.core.transfer.batch._maybe_finish_batch",
            AsyncMock(),
        ) as mocked_finish:
            await handle_batch_item_done(
                task_id="task-2",
                db_id=9,
                share_url="https://115.com/s/ep2",
                share_files=[{"sha": "A2", "name": "E02.mkv"}],
                episode_count=2,
            )

        self.assertEqual(_batch_states["task-2"]["success_count"], 1)
        self.assertEqual(
            _batch_states["task-2"]["share_files"],
            [{"sha": "A1", "name": "E01.mkv"}, {"sha": "A2", "name": "E02.mkv"}],
        )
        mocked_update.assert_awaited_once_with(9, "success")
        mocked_finish.assert_awaited_once_with("task-2", 2)

    async def test_handle_batch_done_emits_single_strm_batch_request_with_accumulated_files(self):
        batch_state = {
            "task_id": "task-3",
            "title": "秘恋稽核中",
            "episode_count": 2,
            "series_folder_id": "cid-3",
            "series_path_str": "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            "share_files": [
                {"sha": "A1", "name": "E01.mkv"},
                {"sha": "A2", "name": "E02.mkv"},
            ],
            "success_count": 2,
            "failed_links": [],
            "group": "transfer-batch:秘恋稽核中",
        }
        _batch_states["task-3"] = dict(batch_state)
        mocked_emit = AsyncMock()

        with patch("app.core.transfer.batch._finalize_transfer_task", AsyncMock()) as mocked_finalize, patch(
            "app.core.transfer.batch._notify_batch_summary",
            AsyncMock(),
        ) as mocked_notify, patch(
            "app.core.transfer.batch.event_bus.emit",
            mocked_emit,
        ):
            await handle_batch_done("task-3", batch_state)

        mocked_finalize.assert_awaited_once_with("task-3", batch_state)
        mocked_notify.assert_awaited_once_with("task-3", batch_state)
        mocked_emit.assert_awaited_once_with(
            "strm.batch.requested",
            task_id="task-3",
            cloud_type="115",
            archive_dir_id="cid-3",
            archive_rel_path="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            strm_rel_dir="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            files=[
                {"sha": "A1", "name": "E01.mkv"},
                {"sha": "A2", "name": "E02.mkv"},
            ],
        )
        self.assertNotIn("task-3", _batch_states)


class StrmBatchPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_strm_for_folder_does_not_duplicate_organized_segments(self):
        generator = StrmGenerator115()
        share_files = [{"sha": "ABC", "name": "大唐迷雾.S01E01.mkv"}]

        with patch.object(generator.client, "list_files", return_value={
            "items": [{"fid": "1", "n": "大唐迷雾.S01E01.mkv", "pc": "pc1", "sha": "ABC"}]
        }), patch("app.database.get_db_conn") as mocked_db_conn, patch.object(
            generator, "generate_strm", return_value=str(Path("strm_output/剧集/国产/大唐迷雾 (2026)/Season 1/大唐迷雾 - S01E01.strm"))
        ) as mocked_generate_strm:
            mocked_db_conn.return_value.__aenter__.return_value.execute.return_value = None
            mocked_db_conn.return_value.__aenter__.return_value.commit.return_value = None

            await generator.generate_strm_for_folder(
                "cid-1",
                share_files,
                strm_subdir="剧集/其他/📺 大唐迷雾 (2026)/Season 1",
                root_output_dir="strm_output",
            )

        self.assertEqual(mocked_generate_strm.await_count, 1)
        args, kwargs = mocked_generate_strm.await_args
        self.assertEqual(args[2], "strm_output")
        self.assertEqual(args[3], "strm_output")
        self.assertTrue(kwargs["skip_organize"])

    async def test_generate_strm_for_folder_records_manifest_fields(self):
        generator = StrmGenerator115()
        share_files = [{"sha": "ABC", "name": "灵魂摆渡·十年.2026.S01E05.mkv"}]
        config = SimpleNamespace(strm=SimpleNamespace(base_url="http://example.com", output_dir="strm_output"))

        mocked_db = patch("app.database.get_db_conn").start()
        self.addCleanup(patch.stopall)
        mocked_db.return_value.__aenter__.return_value.execute.return_value = None
        mocked_db.return_value.__aenter__.return_value.commit.return_value = None

        with patch.object(generator.client, "list_files", return_value={
            "items": [{"fid": "1", "n": "灵魂摆渡·十年.2026.S01E05.mkv", "pc": "pc1", "sha": "ABC"}]
        }), patch("app.core.cloud115.strm.get_config", return_value=config), patch(
            "app.core.cloud115.strm.classify"
        ) as mocked_classify, patch(
            "app.core.cloud115.strm.build_archive_placement"
        ) as mocked_build_placement, patch.object(
            generator, "generate_strm", return_value=str(Path("strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm"))
        ):
            mocked_classify.return_value = SimpleNamespace(
                category="剧集",
                subcategory="国产剧集",
                title="灵魂摆渡·十年",
                year="2026",
                tmdb_id="289271",
                season=1,
                media_type="tv",
            )
            mocked_build_placement.return_value = SimpleNamespace(
                archive_rel_path="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
                strm_rel_dir="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
                target_file_name="灵魂摆渡·十年 - S01E05.mkv",
                strm_file_name="灵魂摆渡·十年 - S01E05.strm",
            )

            await generator.generate_strm_for_folder(
                "cid-9",
                share_files,
                strm_subdir="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
                root_output_dir="strm_output",
            )

        execute_call = mocked_db.return_value.__aenter__.return_value.execute.await_args
        query = execute_call.args[0]
        params = execute_call.args[1]

        self.assertIn("archive_dir_id", query)
        self.assertIn("archive_rel_path", query)
        self.assertIn("strm_rel_path", query)
        self.assertIn("strm_abs_path", query)
        self.assertIn("play_identity", query)
        self.assertEqual(params[0], "115")
        self.assertEqual(params[1], "1")
        self.assertEqual(params[2], "cid-9")
        self.assertEqual(params[3], "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1")
        self.assertEqual(
            params[4],
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
        )
        self.assertEqual(
            params[5],
            "strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
        )
        self.assertEqual(params[6], "pc1")
        self.assertEqual(
            params[8],
            "strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
        )


class Cloud115LocalCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_files_local_first_returns_recursive_items_with_pickcode(self):
        client = Cloud115Client()

        with patch(
            "app.core.cloud115.db_sync.list_local_files",
            return_value=[
                {"id": 10, "parent_id": 0, "name": "电视剧", "is_dir": True, "size": 0, "pickcode": ""},
                {"id": 11, "parent_id": 10, "name": "第一集.mkv", "is_dir": False, "size": 123, "pickcode": "pc-11"},
            ],
        ) as mocked_local:
            result = await client.list_files_local_first("0", limit=100, offset=0, recursive=True)

        mocked_local.assert_called_once_with("0", recursive=True)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"][0], {"cid": "10", "n": "电视剧", "pid": "0"})
        self.assertEqual(
            result["items"][1],
            {"fid": "11", "n": "第一集.mkv", "pid": "10", "pc": "pc-11", "s": 123},
        )

    async def test_batch_generate_requests_non_recursive_local_listing(self):
        generator = StrmGenerator115()

        with patch.object(
            generator.client,
            "list_files_local_first",
            AsyncMock(return_value={"total": 0, "items": []}),
        ) as mocked_list:
            result = await generator.batch_generate("cid-root", "strm_output", "http://example.com")

        self.assertEqual(result, [])
        mocked_list.assert_awaited_once_with(
            dir_id="cid-root",
            limit=1000,
            offset=0,
            recursive=False,
        )


class MediaOrganizerRegionTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_tmdb_strips_display_icon_from_title(self):
        organizer = MediaOrganizer()
        media_info = parse_filename("📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP.mkv")

        with patch("app.core.media.organizer.tmdb_client.search_tv", return_value=[{"id": 1, "name": "秘恋稽核中"}]) as mocked_search_tv:
            tmdb_data = await organizer._search_tmdb(media_info)

        self.assertEqual(tmdb_data["name"], "秘恋稽核中")
        mocked_search_tv.assert_awaited_once_with("秘恋稽核中", 2026)

    async def test_determine_category_and_region_uses_origin_country_for_cn_tv(self):
        organizer = MediaOrganizer()
        media_info = parse_filename("灵魂摆渡·十年.2026.S01E06.mkv")

        category, region = await organizer.determine_category_and_region(
            media_info,
            {"origin_country": ["CN"], "original_language": "ja"},
        )

        self.assertEqual(category, "剧集")
        self.assertEqual(region, "国产")

    async def test_determine_category_and_region_uses_production_countries_for_us_movie(self):
        organizer = MediaOrganizer()
        media_info = parse_filename("Inception.2010.1080p.mkv")

        category, region = await organizer.determine_category_and_region(
            media_info,
            {"production_countries": [{"iso_3166_1": "US"}], "original_language": "zh"},
        )

        self.assertEqual(category, "电影")
        self.assertEqual(region, "欧美")


if __name__ == "__main__":
    unittest.main()