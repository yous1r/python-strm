import unittest

from app.core.transfer.strm_manifest import build_manifest_record


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


if __name__ == "__main__":
    unittest.main()