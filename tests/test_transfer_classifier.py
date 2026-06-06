import unittest
from unittest.mock import patch
from pathlib import Path

from app.core.media.parser import parse_filename
from app.core.transfer.classifier import _sanitize, classify
from app.core.cloud115.strm import StrmGenerator115


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


if __name__ == "__main__":
    unittest.main()