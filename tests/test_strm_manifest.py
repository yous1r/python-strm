import unittest
from unittest.mock import patch

from app.core.transfer.strm_manifest import build_manifest_record, list_records_for_rewrite


class _AsyncCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchall(self):
        return self._rows


class _AsyncDb:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def execute(self, query, params=()):
        self.executed.append((query, params))
        return _AsyncCursor(rows=self.rows)


class _AsyncDbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StrmManifestTests(unittest.TestCase):
    def test_build_manifest_record_keeps_archive_and_strm_paths(self):
        record = build_manifest_record(
            cloud_type="115",
            file_id="fid-1",
            archive_dir_id="cid-9",
            archive_rel_path="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            strm_rel_path="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
            strm_abs_path="/data/strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
            play_identity="pickcode-1",
        )

        self.assertEqual(record["cloud_type"], "115")
        self.assertEqual(record["file_id"], "fid-1")
        self.assertEqual(record["archive_dir_id"], "cid-9")
        self.assertEqual(record["archive_rel_path"], "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1")
        self.assertEqual(record["strm_rel_path"], "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm")
        self.assertEqual(record["strm_abs_path"], "/data/strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm")
        self.assertEqual(record["play_identity"], "pickcode-1")
        self.assertEqual(record["status"], "generated")
        self.assertIn("updated_at", record)
        self.assertTrue(record["updated_at"])


class StrmManifestQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_records_for_rewrite_uses_archive_root_prefix(self):
        db = _AsyncDb(rows=[{"file_id": "fid-1", "archive_rel_path": "剧集/国产剧集"}])

        with patch("app.core.transfer.strm_manifest.get_db_conn", return_value=_AsyncDbContext(db)):
            rows = await list_records_for_rewrite("/剧集/国产剧集/")

        self.assertEqual(rows[0]["file_id"], "fid-1")
        query, params = db.executed[0]
        self.assertIn("archive_rel_path LIKE ?", query)
        self.assertEqual(params, ("剧集/国产剧集%",))


if __name__ == "__main__":
    unittest.main()