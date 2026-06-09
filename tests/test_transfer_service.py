import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.transfer.placement import derive_series_scope_path
from app.services.transfer_service import (
    TransferServiceError,
    overwrite_task_strm,
    receive_share_task,
    rewrite_archive_strm,
)


class _AsyncCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _AsyncDb:
    def __init__(self, task_row, record_rows):
        self.task_row = task_row
        self.record_rows = record_rows

    async def execute(self, query, params):
        if "FROM transfer_tasks" in query:
            return _AsyncCursor(row=self.task_row)
        if "FROM strm_records" in query:
            return _AsyncCursor(rows=self.record_rows)
        raise AssertionError(f"unexpected query: {query}")


class _AsyncDbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OverwriteTaskStrmTests(unittest.IsolatedAsyncioTestCase):
    async def test_overwrite_task_strm_rewrites_files_for_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            strm_path = Path(temp_dir) / "show" / "episode.strm"
            db = _AsyncDb(
                task_row={"task_id": "task-1", "status": "done"},
                record_rows=[
                    {
                        "file_id": "fid-1",
                        "strm_path": str(strm_path),
                        "strm_abs_path": str(strm_path),
                        "strm_rel_path": "show/episode.strm",
                        "play_identity": "pc-1",
                    }
                ],
            )

            async_mock = AsyncMock(return_value=[str(strm_path)])
            with patch("app.services.transfer_service.get_db_conn", return_value=_AsyncDbContext(db)), patch(
                "app.services.transfer_service.generator_115.rewrite_manifest_records",
                async_mock,
            ):
                result = await overwrite_task_strm("task-1")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rewritten_count"], 1)
            async_mock.assert_awaited_once()
            records = async_mock.await_args.args[0]
            self.assertEqual(records[0]["play_identity"], "pc-1")

    async def test_overwrite_task_strm_raises_when_task_missing(self):
        db = _AsyncDb(task_row=None, record_rows=[])
        with patch("app.services.transfer_service.get_db_conn", return_value=_AsyncDbContext(db)):
            with self.assertRaises(TransferServiceError) as ctx:
                await overwrite_task_strm("missing")

        self.assertEqual(ctx.exception.status_code, 404)


class RewriteArchiveStrmTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewrite_archive_strm_delegates_to_manifest_rewrite(self):
        with patch(
            "app.services.transfer_service.list_records_for_rewrite",
            AsyncMock(return_value=[{"file_id": "fid-1"}]),
        ), patch(
            "app.services.transfer_service.generator_115.rewrite_from_manifest",
            AsyncMock(return_value={"archive_root": "剧集/国产剧集", "rewritten": 1, "files": ["/tmp/a.strm"]}),
        ):
            result = await rewrite_archive_strm("剧集/国产剧集")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rewritten"], 1)
        self.assertEqual(result["archive_root"], "剧集/国产剧集")

    async def test_rewrite_archive_strm_raises_when_no_records(self):
        with patch(
            "app.services.transfer_service.list_records_for_rewrite",
            AsyncMock(return_value=[]),
        ):
            with self.assertRaises(TransferServiceError) as ctx:
                await rewrite_archive_strm("剧集/国产剧集")

        self.assertEqual(ctx.exception.status_code, 404)


class ReceiveShareTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_share_task_precreates_archive_path_and_ignores_custom_target_dir(self):
        config = SimpleNamespace(
            transfer=SimpleNamespace(enabled=True, archive_dir_id="archive-root"),
            strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095"),
        )
        mocked_db = _AsyncDbContext(_AsyncDb(task_row=None, record_rows=[]))

        with patch(
            "app.services.transfer_service.get_config",
            return_value=config,
        ), patch(
            "app.services.transfer_service.client_115.get_share_info",
            AsyncMock(return_value={"state": True, "files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]}),
        ), patch(
            "app.services.transfer_service.classify",
            AsyncMock(return_value=SimpleNamespace(
                category="剧集",
                subcategory="日韩剧集",
                title="秘恋稽核中",
                year="2026",
                tmdb_id="297640",
                season=1,
                media_type="tv",
            )),
        ), patch(
            "app.services.transfer_service.client_115.create_path",
            AsyncMock(return_value={"id": "cid-123"}),
        ) as mocked_create_path, patch(
            "app.services.transfer_service.client_115.share_receive",
            AsyncMock(return_value={"state": True, "share_files": [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}]}),
        ) as mocked_receive, patch(
            "app.services.transfer_service.get_db_conn",
            return_value=mocked_db,
        ), patch(
            "app.services.transfer_service.generator_115.generate_strm_for_folder",
            AsyncMock(return_value=["strm_output/a.strm"]),
        ) as mocked_generate_strm, patch(
            "app.services.transfer_service.generator_115.sync_strm_files_from_manifest",
            AsyncMock(return_value={"scanned": 3, "updated": 2, "skipped": 1, "failed": 0}),
        ) as mocked_sync_strm:
            result = await receive_share_task("https://115.com/s/demo", "", target_dir_id="manual-dir")

        self.assertEqual(result["status"], "success")
        mocked_create_path.assert_awaited_once_with(
            "archive-root",
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
        )
        mocked_receive.assert_awaited_once_with(
            "https://115.com/s/demo",
            "",
            target_dir_id="cid-123",
            filter_rules=None,
        )
        mocked_generate_strm.assert_awaited_once_with(
            "cid-123",
            [{"name": "秘恋稽核中 (2026) S01E01.mkv", "sha": "ABC"}],
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1",
            "strm_output",
            task_id=unittest.mock.ANY,
        )
        mocked_sync_strm.assert_awaited_once_with(
            dir_id="cid-123",
            output_dir="strm_output",
            root_output_dir="strm_output",
            base_url="http://localhost:8095",
            archive_root="剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}",
        )
        self.assertEqual(result["strm_updated_count"], 2)

    async def test_receive_share_task_without_archive_dir_raises_instead_of_using_temp_dir(self):
        config = SimpleNamespace(
            transfer=SimpleNamespace(enabled=True, archive_dir_id=""),
        )

        with patch("app.services.transfer_service.get_config", return_value=config):
            with self.assertRaises(TransferServiceError) as ctx:
                await receive_share_task("https://115.com/s/demo", "")

        self.assertEqual(str(ctx.exception), "未配置归档目录 (archive_dir_id)")


class SeriesScopePathTests(unittest.TestCase):
    def test_derive_series_scope_path_trims_trailing_season_segment(self):
        self.assertEqual(
            derive_series_scope_path("剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}/Season 1"),
            "剧集/日韩剧集/秘恋稽核中 (2026) {tmdb-297640}",
        )

    def test_derive_series_scope_path_keeps_non_season_path(self):
        self.assertEqual(
            derive_series_scope_path("电影/国产电影/一部电影 (2024) {tmdb-1}"),
            "电影/国产电影/一部电影 (2024) {tmdb-1}",
        )


if __name__ == "__main__":
    unittest.main()