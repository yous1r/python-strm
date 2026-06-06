import unittest
from unittest.mock import patch

from app.core.media.parser import parse_filename
from app.core.transfer.classifier import classify


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


if __name__ == "__main__":
    unittest.main()