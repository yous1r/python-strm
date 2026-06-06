import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.transfer_service import TransferServiceError, overwrite_task_strm, rewrite_archive_strm


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


if __name__ == "__main__":
    unittest.main()