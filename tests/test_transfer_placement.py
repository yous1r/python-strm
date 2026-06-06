import unittest

from app.core.transfer.models import ClassifyResult
from app.core.transfer.placement import build_archive_placement


class BuildArchivePlacementTests(unittest.TestCase):
    def test_build_archive_placement_for_episode_uses_shared_archive_and_strm_directory(self):
        result = ClassifyResult(
            category="剧集",
            subcategory="国产剧集",
            title="灵魂摆渡·十年",
            year="2026",
            tmdb_id="289271",
            season=1,
            media_type="tv",
        )

        placement = build_archive_placement(result, "灵魂摆渡·十年.2026.S01E05.mkv")

        self.assertEqual(
            placement.archive_rel_path,
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
        )
        self.assertEqual(
            placement.strm_rel_dir,
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
        )
        self.assertEqual(placement.target_file_name, "灵魂摆渡·十年 - S01E05.mkv")
        self.assertEqual(placement.strm_file_name, "灵魂摆渡·十年 - S01E05.strm")

    def test_build_archive_placement_for_movie_uses_final_folder_name_for_files(self):
        result = ClassifyResult(
            category="电影",
            subcategory="",
            title="盗梦空间",
            year="2010",
            tmdb_id="27205",
            season=0,
            media_type="movie",
        )

        placement = build_archive_placement(result, "Inception.2010.1080p.mkv")

        self.assertEqual(placement.archive_rel_path, "电影/盗梦空间 (2010) {tmdb-27205}")
        self.assertEqual(placement.strm_rel_dir, "电影/盗梦空间 (2010) {tmdb-27205}")
        self.assertEqual(placement.target_file_name, "盗梦空间 (2010) {tmdb-27205}.mkv")
        self.assertEqual(placement.strm_file_name, "盗梦空间 (2010) {tmdb-27205}.strm")


    def test_build_archive_placement_for_episode_keeps_directory_and_file_season_in_sync(self):
        result = ClassifyResult(
            category="剧集",
            subcategory="国产剧集",
            title="灵魂摆渡·十年",
            year="2026",
            tmdb_id="289271",
            season=2,
            media_type="tv",
        )

        placement = build_archive_placement(result, "灵魂摆渡·十年.2026.S01E05.mkv")

        self.assertEqual(
            placement.archive_rel_path,
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 2",
        )
        self.assertEqual(
            placement.strm_rel_dir,
            "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 2",
        )
        self.assertEqual(placement.target_file_name, "灵魂摆渡·十年 - S02E05.mkv")
        self.assertEqual(placement.strm_file_name, "灵魂摆渡·十年 - S02E05.strm")


if __name__ == "__main__":
    unittest.main()

