import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace

from app.core.cloud115.client import Cloud115Client
from app.core.media.parser import parse_filename
from app.core.transfer.classifier import _sanitize, classify
from app.core.transfer.batch import (
    _batch_completion_states,
    _batch_states,
    _build_batch_sample_name,
    _finalize_transfer_task,
    handle_batch_db_sync_completed,
    handle_batch_db_sync_requested,
    handle_batch_done,
    handle_batch_item_done,
    handle_strm_batch_completed,
    handle_strm_batch_rewrite_requested,
    handle_batch_requested,
)
from app.core.cloud115.strm import StrmGenerator115
from app.core.media.organizer import MediaOrganizer


def _cloud_plugin(client=None, generator=None, sync_directory=None):
    return SimpleNamespace(
        client=client or SimpleNamespace(),
        strm_generator=generator or SimpleNamespace(),
        sync_directory=sync_directory or AsyncMock(return_value=0),
    )


class _RecordingDb:
    def __init__(self):
        self.calls = []
        self.commits = 0

    async def execute(self, query, params=()):
        self.calls.append((query, params))

    async def commit(self):
        self.commits += 1


class _RecordingDbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class ParseFilenameTests(unittest.TestCase):
    def test_parse_filename_detects_episode_without_video_extension(self):
        info = parse_filename("📺 秘恋稽核中 (2026) S01E01 1080P WEB-DL DDP (2026)")

        self.assertEqual(info.media_type, "episode")
        self.assertEqual(info.season, 1)
        self.assertEqual(info.episode, 1)


class ClassifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_treats_extensionless_episode_title_as_tv(self):
        config = SimpleNamespace(
            transfer=SimpleNamespace(
                categories=[
                    SimpleNamespace(name="电影", subcategories=["国产电影", "欧美电影", "日韩电影", "其他"]),
                    SimpleNamespace(name="剧集", subcategories=["国产剧集", "欧美剧集", "日韩剧集", "其他"]),
                ],
                default_categories=lambda: [],
            )
        )
        tmdb_data = {
            "id": 297640,
            "name": "秘恋稽核中",
            "first_air_date": "2026-01-01",
        }

        with patch(
            "app.core.transfer.classifier.get_config",
            return_value=config,
        ), patch(
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

    async def test_classify_prefers_frontend_configured_subcategory_order(self):
        config = SimpleNamespace(
            transfer=SimpleNamespace(
                categories=[
                    SimpleNamespace(name="剧集", subcategories=["国产=华语剧", "欧美=欧美精选", "日韩=日韩精选", "其他=未分类"]),
                ],
                default_categories=lambda: [],
            )
        )

        with patch(
            "app.core.transfer.classifier.get_config",
            return_value=config,
        ), patch(
            "app.core.transfer.classifier.media_organizer.get_organized_path",
            return_value=("剧集", "日韩", "秘恋稽核中 (2026)/Season 01", "秘恋稽核中 - S01E01", {"id": 297640, "name": "秘恋稽核中", "first_air_date": "2026-01-01"}),
        ):
            result = await classify("秘恋稽核中 (2026) S01E01.mkv")

        self.assertIsNotNone(result)
        self.assertEqual(result.subcategory, "日韩精选")

    def test_sanitize_removes_display_prefix_from_fallback_title(self):
        self.assertEqual(_sanitize("📺 大唐迷雾 (2026)"), "大唐迷雾 (2026)")


class BatchPrepareTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        _batch_states.clear()
        _batch_completion_states.clear()

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
            transfer=SimpleNamespace(),
        )

        mocked_create_path = AsyncMock(return_value={"id": "cid-123"})
        with patch("app.core.transfer.batch.classify", AsyncMock(return_value=classify_result)) as mocked_classify, patch(
            "app.core.transfer.batch.get_cloud_plugin",
            return_value=_cloud_plugin(client=SimpleNamespace(create_path=mocked_create_path)),
        ), patch(
            "app.core.transfer.batch.get_config",
            return_value=config,
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            mocked_emit,
        ):
            await handle_batch_requested(
                "task-1",
                rows,
                base_title="秘恋稽核中",
                target_dir_id="archive-root",
                target_dir_name="115 STRM 扫描源目录",
            )

        mocked_classify.assert_awaited_once_with("秘恋稽核中 (2026) S01E01")
        mocked_create_path.assert_awaited_once_with(
            "archive-root",
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
        )
        self.assertEqual(_batch_states["task-1"]["series_path_str"], "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1")
        mocked_emit.assert_awaited_once()

    async def test_handle_batch_requested_uses_selected_strm_destination_root(self):
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
        config = SimpleNamespace(transfer=SimpleNamespace())

        mocked_create_path = AsyncMock(return_value={"id": "cid-123"})
        with patch("app.core.transfer.batch.classify", AsyncMock(return_value=classify_result)), patch(
            "app.core.transfer.batch.get_cloud_plugin",
            return_value=_cloud_plugin(client=SimpleNamespace(create_path=mocked_create_path)),
        ), patch(
            "app.core.transfer.batch.get_config",
            return_value=config,
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            mocked_emit,
        ):
            await handle_batch_requested(
                "task-selected-root",
                rows,
                base_title="秘恋稽核中",
                cloud_type="115",
                target_dir_id="strm-root",
                target_dir_name="115 STRM 扫描源目录",
            )

        mocked_create_path.assert_awaited_once_with(
            "strm-root",
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
        )
        self.assertEqual(_batch_states["task-selected-root"]["target_dir_id"], "cid-123")
        self.assertEqual(_batch_states["task-selected-root"]["destination_dir_id"], "strm-root")
        self.assertEqual(_batch_states["task-selected-root"]["target_dir_name"], "115 STRM 扫描源目录")
        mocked_emit.assert_awaited_once()
        _, kwargs = mocked_emit.await_args
        self.assertEqual(kwargs["target_dir_id"], "cid-123")
        self.assertEqual(kwargs["destination_dir_id"], "strm-root")
        self.assertEqual(kwargs["cloud_type"], "115")

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
            "target_dir_id": "cid-3",
            "series_path_str": "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            "share_files": [
                {"sha": "A1", "name": "E01.mkv"},
                {"sha": "A2", "name": "E02.mkv"},
            ],
            "cloud_type": "115",
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
            "transfer_batch_db_sync_requested",
            task_id="task-3",
            archive_dir_id="cid-3",
            archive_rel_path="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            strm_rel_dir="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            batch_title="秘恋稽核中",
            files=[
                {"sha": "A1", "name": "E01.mkv"},
                {"sha": "A2", "name": "E02.mkv"},
            ],
            cloud_type="115",
        )
        self.assertNotIn("task-3", _batch_states)
        self.assertEqual(_batch_completion_states["task-3"]["task_id"], "task-3")

    async def test_finalize_transfer_task_keeps_successful_batch_running_until_strm_completed(self):
        db = _RecordingDb()
        batch_state = {
            "episode_count": 2,
            "success_count": 2,
            "failed_links": [],
            "target_dir_id": "cid-8",
        }

        with patch("app.core.transfer.batch.get_db_conn", return_value=_RecordingDbContext(db)):
            await _finalize_transfer_task("task-8", batch_state)

        query, params = db.calls[0]
        self.assertIn("UPDATE transfer_tasks", query)
        self.assertEqual(params[0], "running")
        self.assertNotIn("completed_at=CURRENT_TIMESTAMP", query)

    async def test_handle_strm_batch_completed_marks_task_done_after_ingest(self):
        db = _RecordingDb()
        _batch_completion_states["task-9"] = {
            "task_id": "task-9",
            "success_count": 2,
            "failed_links": [],
        }

        with patch("app.core.transfer.batch.get_db_conn", return_value=_RecordingDbContext(db)):
            await handle_strm_batch_completed(
                task_id="task-9",
                archive_dir_id="cid-9",
                archive_rel_path="剧集/国产剧集/示例剧/Season 1",
                files=[],
                strm_stats={"failed": 0},
            )

        query, params = db.calls[0]
        self.assertIn("completed_at=CURRENT_TIMESTAMP", query)
        self.assertEqual(params, ("done", "task-9"))
        self.assertNotIn("task-9", _batch_completion_states)

    async def test_handle_batch_db_sync_requested_emits_followup_event_after_sync(self):
        mocked_emit = AsyncMock()

        mocked_sync = AsyncMock(return_value=6)
        with patch(
            "app.core.transfer.batch.get_cloud_plugin",
            return_value=_cloud_plugin(sync_directory=mocked_sync),
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            mocked_emit,
        ):
            await handle_batch_db_sync_requested(
                task_id="task-4",
                archive_dir_id="cid-4",
                archive_rel_path="剧集/国产剧集/示例剧/Season 1",
                strm_rel_dir="剧集/国产剧集/示例剧/Season 1",
                batch_title="示例剧",
                files=[{"sha": "A1", "name": "E01.mkv"}],
            )

        mocked_sync.assert_awaited_once_with("cid-4", "剧集/国产剧集/示例剧/Season 1", recursive=True)
        mocked_emit.assert_awaited_once_with(
            "transfer_batch_db_sync_completed",
            task_id="task-4",
            archive_dir_id="cid-4",
            archive_rel_path="剧集/国产剧集/示例剧/Season 1",
            strm_rel_dir="剧集/国产剧集/示例剧/Season 1",
            batch_title="示例剧",
            files=[{"sha": "A1", "name": "E01.mkv"}],
            count=6,
            cloud_type="115",
        )

    async def test_handle_batch_db_sync_completed_emits_strm_request(self):
        mocked_emit = AsyncMock()

        with patch("app.core.transfer.batch.event_bus.emit", mocked_emit):
            await handle_batch_db_sync_completed(
                task_id="task-5",
                archive_dir_id="cid-5",
                archive_rel_path="剧集/国产剧集/示例剧/Season 1",
                strm_rel_dir="剧集/国产剧集/示例剧/Season 1",
                files=[{"sha": "A1", "name": "E01.mkv"}],
                count=3,
            )

        mocked_emit.assert_awaited_once_with(
            "strm_batch_requested",
            task_id="task-5",
            cloud_type="115",
            archive_dir_id="cid-5",
            archive_rel_path="剧集/国产剧集/示例剧/Season 1",
            strm_rel_dir="剧集/国产剧集/示例剧/Season 1",
            files=[{"sha": "A1", "name": "E01.mkv"}],
        )

    async def test_handle_strm_batch_requested_emits_rewrite_event_after_manifest_refresh(self):
        config = SimpleNamespace(strm=SimpleNamespace(base_url="http://example.com", output_dir="strm_output"))

        mocked_manifest = AsyncMock(return_value={"scanned": 2, "changed": True})
        with patch("app.core.transfer.batch.get_config", return_value=config), patch(
            "app.core.transfer.batch.get_cloud_plugin",
            return_value=_cloud_plugin(generator=SimpleNamespace(sync_manifest_records=mocked_manifest)),
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            AsyncMock(),
        ) as mocked_emit:
            from app.core.transfer.batch import handle_strm_batch_requested

            await handle_strm_batch_requested(
                task_id="task-6",
                archive_dir_id="cid-6",
                archive_rel_path="剧集/国产剧集/示例剧/Season 1",
                strm_rel_dir="剧集/国产剧集/示例剧/Season 1",
                files=[{"sha": "A1", "name": "E01.mkv"}],
            )

        mocked_manifest.assert_awaited_once_with(
            dir_id="cid-6",
            dir_name="剧集/国产剧集/示例剧/Season 1",
            output_dir="strm_output/剧集/国产剧集/示例剧/Season 1",
            base_url="http://example.com",
            recursive=True,
            root_output_dir="strm_output",
            preserve_existing_structure=True,
        )
        mocked_emit.assert_awaited_once_with(
            "strm_batch_rewrite_requested",
            task_id="task-6",
            cloud_type="115",
            archive_dir_id="cid-6",
            archive_rel_path="剧集/国产剧集/示例剧/Season 1",
            files=[{"sha": "A1", "name": "E01.mkv"}],
            manifest_stats={"scanned": 2, "changed": True},
        )

    async def test_handle_strm_batch_rewrite_requested_runs_strm_refresh_then_emits_completed(self):
        config = SimpleNamespace(strm=SimpleNamespace(base_url="http://example.com", output_dir="strm_output"))

        mocked_strm = AsyncMock(return_value={"updated": 2, "files": ["a.strm"], "skipped": 1, "failed": 0})
        with patch("app.core.transfer.batch.get_config", return_value=config), patch(
            "app.core.transfer.batch.get_cloud_plugin",
            return_value=_cloud_plugin(generator=SimpleNamespace(sync_strm_files_from_manifest=mocked_strm)),
        ), patch(
            "app.core.transfer.batch.event_bus.emit",
            AsyncMock(),
        ) as mocked_emit:
            await handle_strm_batch_rewrite_requested(
                task_id="task-7",
                archive_dir_id="cid-7",
                archive_rel_path="剧集/国产剧集/示例剧/Season 1",
                files=[{"sha": "A1", "name": "E01.mkv"}],
                manifest_stats={"scanned": 2, "changed": True},
            )

        mocked_strm.assert_awaited_once_with(
            dir_id="cid-7",
            output_dir="strm_output/剧集/国产剧集/示例剧/Season 1",
            root_output_dir="strm_output",
            base_url="http://example.com",
            archive_root="剧集/国产剧集/示例剧",
        )
        mocked_emit.assert_awaited_once_with(
            "strm_batch_completed",
            task_id="task-7",
            cloud_type="115",
            archive_dir_id="cid-7",
            archive_rel_path="剧集/国产剧集/示例剧/Season 1",
            files=[{"sha": "A1", "name": "E01.mkv"}],
            manifest_stats={"scanned": 2, "changed": True},
            strm_stats={"updated": 2, "files": ["a.strm"], "skipped": 1, "failed": 0},
        )


class StrmBatchPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_strm_for_folder_does_not_duplicate_organized_segments(self):
        generator = StrmGenerator115()
        share_files = [{"sha": "ABC", "name": "大唐迷雾.S01E01.mkv"}]

        with patch("app.core.cloud115.strm.list_local_files", return_value=[
            {"id": "1", "parent_id": "cid-1", "name": "大唐迷雾.S01E01.mkv", "is_dir": False, "size": 1, "pickcode": "pc1", "sha": "ABC"}
        ]), patch("app.database.get_db_conn") as mocked_db_conn, patch.object(
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

        mocked_db = patch("app.core.cloud115.strm.get_db_conn").start()
        self.addCleanup(patch.stopall)
        mocked_db.return_value.__aenter__.return_value.execute.return_value = None
        mocked_db.return_value.__aenter__.return_value.commit.return_value = None

        with patch("app.core.cloud115.strm.list_local_files", return_value=[
            {"id": "1", "parent_id": "cid-9", "name": "灵魂摆渡·十年.2026.S01E05.mkv", "is_dir": False, "size": 1, "pickcode": "pc1", "sha": "ABC"}
        ]), patch("app.core.cloud115.strm.get_config", return_value=config), patch(
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
        self.assertIn("source_file_name", query)
        self.assertIn("source_pickcode", query)
        self.assertIn("source_sha", query)
        self.assertIn("source_archive_dir_id", query)
        self.assertEqual(params[0], "115")
        self.assertEqual(params[1], "1")
        self.assertEqual(params[2], "灵魂摆渡·十年.2026.S01E05.mkv")
        self.assertEqual(params[3], "pc1")
        self.assertEqual(params[4], "ABC")
        self.assertEqual(params[5], "cid-9")
        self.assertEqual(
            params[7],
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
        )
        self.assertEqual(
            params[8],
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
        )
        self.assertEqual(
            params[9],
            "strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
        )
        self.assertEqual(params[6], "cid-9")
        self.assertEqual(params[10], "pc1")
        self.assertEqual(
            params[12],
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

        with patch(
            "app.core.cloud115.strm.list_local_files",
            return_value=[],
        ) as mocked_list, patch(
            "app.core.cloud115.strm.get_config",
            return_value=SimpleNamespace(
                strm=SimpleNamespace(clean_invalid=False),
                organize=SimpleNamespace(enabled=False),
                cloud115=SimpleNamespace(play_ua=""),
            ),
        ):
            result = await generator.batch_generate("cid-root", "strm_output", "http://example.com")

        self.assertEqual(result, [])
        mocked_list.assert_called_once_with("cid-root", recursive=False)


class TransferPipelineInitTests(unittest.TestCase):
    def test_init_transfer_pipeline_registers_batch_events_without_legacy_transfer_enabled(self):
        from app.core.transfer import init_transfer_pipeline

        config = SimpleNamespace(
            transfer=SimpleNamespace(
                enabled=False,
                temp_dir_id="",
                archive_dir_id="",
                inbox_dir_id="0",
            )
        )

        with patch("app.core.transfer.get_config", return_value=config), \
             patch("app.core.transfer.batch.init_batch_transfer") as mocked_batch, \
             patch("app.core.transfer.rollback.init_rollback") as mocked_rollback, \
             patch("app.core.transfer.mover.init_mover") as mocked_mover, \
             patch("app.core.transfer.organizer.init_organizer") as mocked_organizer, \
             patch("app.core.transfer.scope.init_scope") as mocked_scope:
            init_transfer_pipeline()

        mocked_batch.assert_called_once()
        mocked_rollback.assert_called_once()
        mocked_mover.assert_not_called()
        mocked_organizer.assert_not_called()
        mocked_scope.assert_not_called()


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
