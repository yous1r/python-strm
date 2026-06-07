import tempfile
import unittest
from pathlib import Path

from app.config import EmbyProxyInstanceConfig, EmbyStrmPathMappingConfig
from app.core.emby.standalone_proxy import _resolve_local_strm_path


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