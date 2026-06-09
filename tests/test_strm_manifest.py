import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.transfer.strm_manifest import (
    build_manifest_record,
    get_media_item_link,
    get_strm_record_by_file_id,
    hydrate_playback_record,
    list_records_for_rewrite,
    upsert_media_item_link,
)


class _AsyncCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _AsyncDb:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def execute(self, query, params=()):
        self.executed.append((query, params))
        return _AsyncCursor(rows=self.rows)

    async def commit(self):
        self.executed.append(("COMMIT", ()))


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

    async def test_hydrate_playback_record_backfills_pickcode_from_local_cache(self):
        db = _AsyncDb(rows=[])
        record = {
            "id": 3,
            "cloud_type": "115",
            "file_id": "fid-3",
            "play_identity": "",
        }

        with (
            patch("app.core.transfer.strm_manifest.get_local_pickcode", return_value="pickcode-3"),
            patch("app.core.transfer.strm_manifest.get_db_conn", return_value=_AsyncDbContext(db)),
        ):
            hydrated = await hydrate_playback_record(record)

        self.assertEqual(hydrated["play_identity"], "pickcode-3")
        query, params = db.executed[0]
        self.assertIn("UPDATE strm_records", query)
        self.assertEqual(params[2:], ("115", "fid-3"))
        self.assertEqual(db.executed[-1], ("COMMIT", ()))


class StrmGeneratorBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_generate_uses_non_recursive_local_listing_per_level(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()

        responses = {
            "root": [
                {"id": "child", "parent_id": "root", "name": "Season 1", "is_dir": True, "size": 0, "pickcode": "", "sha": ""},
            ],
            "child": [
                {"id": "f-1", "parent_id": "child", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
            ],
        }
        visited: list[tuple[str, bool]] = []
        recorded: list[dict] = []

        def fake_list_local_files(dir_id, recursive=False):
            visited.append((dir_id, recursive))
            return responses[dir_id]

        async def fake_generate_strm(*args, **kwargs):
            return "/tmp/Season 1/Episode 01.strm"

        async def fake_record_manifest(**kwargs):
            recorded.append(kwargs)

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "generate_strm", side_effect=fake_generate_strm),
            patch.object(generator, "_record_manifest", side_effect=fake_record_manifest),
            patch.object(generator, "_cleanup_invalid_strm_records", AsyncMock(return_value={"records": 0, "files": 0})),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
        ):
            await generator.batch_generate(
                dir_id="root",
                output_dir="/tmp",
                base_url="http://localhost:8095",
                recursive=True,
                root_output_dir="/tmp",
                force=True,
            )

        self.assertEqual(visited, [("root", False), ("child", False)])
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["archive_dir_id"], "child")

    async def test_batch_generate_still_descends_when_level_has_only_subdirectories(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()

        responses = {
            "root": [
                {"id": "child", "parent_id": "root", "name": "剧集", "is_dir": True, "size": 0, "pickcode": "", "sha": ""},
            ],
            "child": [
                {"id": "f-2", "parent_id": "child", "name": "Episode 02.mkv", "is_dir": False, "size": 1, "pickcode": "pc-2", "sha": ""},
            ],
        }
        recorded: list[dict] = []

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_generate_strm(*args, **kwargs):
            return "/tmp/剧集/Episode 02.strm"

        async def fake_record_manifest(**kwargs):
            recorded.append(kwargs)

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "generate_strm", side_effect=fake_generate_strm),
            patch.object(generator, "_record_manifest", side_effect=fake_record_manifest),
            patch.object(generator, "_cleanup_invalid_strm_records", AsyncMock(return_value={"records": 0, "files": 0})),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
        ):
            generated = await generator.batch_generate(
                dir_id="root",
                output_dir="/tmp",
                base_url="http://localhost:8095",
                recursive=True,
                root_output_dir="/tmp",
                force=False,
            )

        self.assertEqual(generated, ["/tmp/剧集/Episode 02.strm"])
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["archive_dir_id"], "child")

    async def test_cleanup_invalid_strm_records_removes_stale_files_and_records(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()

        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir) / "strm_output"
            scope_dir = root_dir / "综艺"
            stale_file = scope_dir / "stale.strm"
            alive_file = scope_dir / "alive.strm"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_text("stale", encoding="utf-8")
            alive_file.write_text("alive", encoding="utf-8")

            records = [
                {
                    "id": 1,
                    "file_id": "alive-fid",
                    "strm_abs_path": str(alive_file),
                    "strm_path": str(alive_file),
                },
                {
                    "id": 2,
                    "file_id": "stale-fid",
                    "strm_abs_path": str(stale_file),
                    "strm_path": str(stale_file),
                },
            ]

            with (
                patch("app.core.cloud115.strm.list_local_files", return_value=[{"id": "alive-fid", "is_dir": False}]),
                patch.object(generator, "_load_cleanup_candidates", AsyncMock(return_value=records)),
                patch.object(generator, "_delete_manifest_records", AsyncMock(return_value=1)) as mocked_delete,
            ):
                result = await generator._cleanup_invalid_strm_records(
                    dir_id="scope-dir",
                    output_dir=str(scope_dir),
                    root_output_dir=str(root_dir),
                )

                self.assertFalse(stale_file.exists())
                self.assertTrue(alive_file.exists())

        self.assertEqual(result, {"records": 1, "files": 1})
        mocked_delete.assert_awaited_once_with([records[1]])

    async def test_sync_manifest_records_tracks_created_updated_and_unchanged(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
                {"id": "f-2", "parent_id": "root", "name": "Episode 02.mkv", "is_dir": False, "size": 1, "pickcode": "pc-2", "sha": ""},
                {"id": "f-3", "parent_id": "root", "name": "Episode 03.mkv", "is_dir": False, "size": 1, "pickcode": "pc-3", "sha": ""},
            ]
        }
        existing_records = {
            "f-2": {
                "file_id": "f-2",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 02.strm",
                "strm_abs_path": "/tmp/剧集/Episode 02.strm",
                "play_identity": "pc-2",
                "status": "generated",
            },
            "f-3": {
                "file_id": "f-3",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/old.strm",
                "strm_abs_path": "/tmp/剧集/old.strm",
                "play_identity": "pc-old",
                "status": "generated",
            },
        }
        desired_records = {
            "f-1": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/Episode 01.strm",
                "play_identity": "pc-new",
                "status": "generated",
            },
            "f-2": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 02.strm",
                "strm_abs_path": "/tmp/剧集/Episode 02.strm",
                "play_identity": "pc-same",
                "status": "generated",
            },
            "f-3": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 03.strm",
                "strm_abs_path": "/tmp/剧集/Episode 03.strm",
                "play_identity": "pc-updated",
                "status": "generated",
            },
        }
        recorded: list[dict] = []

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_load_existing(file_ids):
            return {file_id: existing_records[file_id] for file_id in file_ids if file_id in existing_records}

        async def fake_build_manifest(**kwargs):
            record = dict(desired_records[kwargs["file_id"]])
            record["file_id"] = kwargs["file_id"]
            return record

        async def fake_record_manifest(**kwargs):
            recorded.append(kwargs)

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", side_effect=fake_load_existing),
            patch.object(generator, "_build_manifest_record_for_item", side_effect=fake_build_manifest),
            patch.object(generator, "_record_manifest", side_effect=fake_record_manifest),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch("app.core.cloud115.strm.get_config", return_value=type("Config", (), {"strm": type("Strm", (), {"clean_invalid": False})(), "organize": type("Organize", (), {"enabled": False})()})()),
        ):
            result = await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/剧集",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
            )

        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertTrue(result["changed"])
        self.assertEqual(len(recorded), 2)

    async def test_sync_manifest_records_emits_debug_logs_for_created_updated_and_unchanged(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
                {"id": "f-2", "parent_id": "root", "name": "Episode 02.mkv", "is_dir": False, "size": 1, "pickcode": "pc-2", "sha": ""},
                {"id": "f-3", "parent_id": "root", "name": "Episode 03.mkv", "is_dir": False, "size": 1, "pickcode": "pc-3", "sha": ""},
            ]
        }
        existing_records = {
            "f-2": {
                "file_id": "f-2",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 02.strm",
                "strm_abs_path": "/tmp/剧集/Episode 02.strm",
                "play_identity": "pc-2",
                "status": "generated",
            },
            "f-3": {
                "file_id": "f-3",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/old.strm",
                "strm_abs_path": "/tmp/剧集/old.strm",
                "play_identity": "pc-old",
                "status": "generated",
            },
        }
        desired_records = {
            "f-1": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/Episode 01.strm",
                "play_identity": "pc-new",
                "status": "generated",
            },
            "f-2": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 02.strm",
                "strm_abs_path": "/tmp/剧集/Episode 02.strm",
                "play_identity": "pc-same",
                "status": "generated",
            },
            "f-3": {
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 03.strm",
                "strm_abs_path": "/tmp/剧集/Episode 03.strm",
                "play_identity": "pc-updated",
                "status": "generated",
            },
        }

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_load_existing(file_ids):
            return {file_id: existing_records[file_id] for file_id in file_ids if file_id in existing_records}

        async def fake_build_manifest(**kwargs):
            record = dict(desired_records[kwargs["file_id"]])
            record["file_id"] = kwargs["file_id"]
            return record

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", side_effect=fake_load_existing),
            patch.object(generator, "_build_manifest_record_for_item", side_effect=fake_build_manifest),
            patch.object(generator, "_record_manifest", AsyncMock()),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch("app.core.cloud115.strm.get_config", return_value=type("Config", (), {"strm": type("Strm", (), {"clean_invalid": False})(), "organize": type("Organize", (), {"enabled": False})()})()),
            patch("app.core.cloud115.strm.logger.debug") as debug_mock,
        ):
            await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/剧集",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
            )

        logged_messages = [call.args[0] for call in debug_mock.call_args_list if call.args]
        self.assertTrue(any("manifest created" in message and "f-1" in message for message in logged_messages))
        self.assertTrue(any("manifest updated" in message and "f-3" in message for message in logged_messages))
        self.assertFalse(any("manifest unchanged" in message and "f-2" in message for message in logged_messages))
        self.assertTrue(any("manifest sync summary" in message and "unchanged=1" in message for message in logged_messages))

    async def test_sync_manifest_records_reuses_existing_record_when_signature_matches(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": "sha-1"},
            ]
        }
        existing_records = {
            "f-1": {
                "file_id": "f-1",
                "source_file_name": "Episode 01.mkv",
                "source_pickcode": "pc-1",
                "source_sha": "SHA-1",
                "source_archive_dir_id": "root",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/Episode 01.strm",
                "play_identity": "pc-1",
                "status": "generated",
            }
        }

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_load_existing(file_ids):
            return {file_id: existing_records[file_id] for file_id in file_ids if file_id in existing_records}

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", side_effect=fake_load_existing),
            patch.object(generator, "_build_manifest_record_for_item", AsyncMock(side_effect=AssertionError("should not rebuild"))),
            patch.object(generator, "_record_manifest", AsyncMock()),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch("app.core.cloud115.strm.get_config", return_value=type("Config", (), {"strm": type("Strm", (), {"clean_invalid": False})(), "organize": type("Organize", (), {"enabled": True})()})()),
        ):
            result = await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/剧集",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
            )

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)

    async def test_sync_manifest_records_reuses_legacy_record_without_signature_when_scope_matches(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
            ]
        }
        existing_records = {
            "f-1": {
                "file_id": "f-1",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/Episode 01.strm",
                "play_identity": "pc-1",
                "status": "generated",
            }
        }

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_load_existing(file_ids):
            return {file_id: existing_records[file_id] for file_id in file_ids if file_id in existing_records}

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", side_effect=fake_load_existing),
            patch.object(generator, "_build_manifest_record_for_item", AsyncMock(side_effect=AssertionError("should not rebuild legacy record"))),
            patch.object(generator, "_record_manifest", AsyncMock()),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch("app.core.cloud115.strm.get_config", return_value=type("Config", (), {"strm": type("Strm", (), {"clean_invalid": False})(), "organize": type("Organize", (), {"enabled": False})()})()),
        ):
            result = await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/剧集",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
            )

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)

    async def test_sync_manifest_records_rebuilds_legacy_record_without_signature_when_organize_enabled(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
            ]
        }
        existing_records = {
            "f-1": {
                "file_id": "f-1",
                "archive_dir_id": "root",
                "archive_rel_path": "剧集",
                "strm_rel_path": "剧集/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/Episode 01.strm",
                "play_identity": "pc-1",
                "status": "generated",
            }
        }
        recorded: list[dict] = []

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        async def fake_load_existing(file_ids):
            return {file_id: existing_records[file_id] for file_id in file_ids if file_id in existing_records}

        async def fake_build_manifest(**kwargs):
            return {
                "file_id": kwargs["file_id"],
                "archive_dir_id": "root",
                "archive_rel_path": "剧集/国产",
                "strm_rel_path": "剧集/国产/Episode 01.strm",
                "strm_abs_path": "/tmp/剧集/国产/Episode 01.strm",
                "play_identity": "pc-1",
                "status": "generated",
            }

        async def fake_record_manifest(**kwargs):
            recorded.append(kwargs)

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", side_effect=fake_load_existing),
            patch.object(generator, "_build_manifest_record_for_item", side_effect=fake_build_manifest),
            patch.object(generator, "_record_manifest", side_effect=fake_record_manifest),
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch("app.core.cloud115.strm.get_config", return_value=type("Config", (), {"strm": type("Strm", (), {"clean_invalid": False})(), "organize": type("Organize", (), {"enabled": True})()})()),
        ):
            result = await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/剧集",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
            )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 0)
        self.assertEqual(recorded[0]["source_file_name"], "Episode 01.mkv")

    async def test_sync_manifest_records_preserves_scope_when_requested_even_if_organize_enabled(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()
        responses = {
            "root": [
                {"id": "f-1", "parent_id": "root", "name": "Episode 01.mkv", "is_dir": False, "size": 1, "pickcode": "pc-1", "sha": ""},
            ]
        }

        def fake_list_local_files(dir_id, recursive=False):
            return responses[dir_id]

        with (
            patch("app.core.cloud115.strm.list_local_files", side_effect=fake_list_local_files),
            patch.object(generator, "_load_existing_records_by_file_ids", AsyncMock(return_value={})),
            patch.object(generator, "_record_manifest", AsyncMock()) as record_mock,
            patch("app.core.cloud115.strm.is_video_file", return_value=True),
            patch(
                "app.core.cloud115.strm.get_config",
                return_value=type(
                    "Config",
                    (),
                    {
                        "strm": type("Strm", (), {"clean_invalid": False})(),
                        "organize": type("Organize", (), {"enabled": True})(),
                    },
                )(),
            ),
        ):
            result = await generator.sync_manifest_records(
                dir_id="root",
                output_dir="/tmp/电影/国产电影/一部未完成的电影 (2024) {tmdb-878607}",
                base_url="http://localhost:8095",
                recursive=False,
                root_output_dir="/tmp",
                preserve_existing_structure=True,
            )

        self.assertEqual(result["created"], 1)
        recorded_kwargs = record_mock.await_args.kwargs
        self.assertEqual(
            recorded_kwargs["archive_rel_path"],
            "电影/国产电影/一部未完成的电影 (2024) {tmdb-878607}",
        )
        self.assertEqual(
            recorded_kwargs["strm_rel_path"],
            "电影/国产电影/一部未完成的电影 (2024) {tmdb-878607}/Episode 01.strm",
        )

    async def test_sync_strm_files_from_manifest_skips_unchanged_content(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()

        with tempfile.TemporaryDirectory() as temp_dir:
            strm_file = Path(temp_dir) / "Episode 01.strm"
            strm_file.write_text("expected-content", encoding="utf-8")
            records = [{
                "strm_abs_path": str(strm_file),
                "strm_rel_path": "Episode 01.strm",
                "play_identity": "pc-1",
            }]

            with (
                patch.object(generator, "list_manifest_records_for_scope", AsyncMock(return_value=records)),
                patch.object(generator, "build_strm_content", return_value="expected-content"),
            ):
                result = await generator.sync_strm_files_from_manifest(
                    dir_id="root",
                    output_dir=temp_dir,
                    root_output_dir=temp_dir,
                    base_url="http://localhost:8095",
                )

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)

    async def test_sync_strm_files_from_manifest_emits_debug_logs_for_skipped_and_written_files(self):
        from app.core.cloud115.strm import StrmGenerator115

        generator = StrmGenerator115()

        with tempfile.TemporaryDirectory() as temp_dir:
            skipped_file = Path(temp_dir) / "Episode 01.strm"
            skipped_file.write_text("expected-content", encoding="utf-8")
            updated_file = Path(temp_dir) / "Episode 02.strm"
            records = [
                {
                    "file_id": "f-1",
                    "strm_abs_path": str(skipped_file),
                    "strm_rel_path": "Episode 01.strm",
                    "play_identity": "pc-1",
                },
                {
                    "file_id": "f-2",
                    "strm_abs_path": str(updated_file),
                    "strm_rel_path": "Episode 02.strm",
                    "play_identity": "pc-2",
                },
            ]

            with (
                patch.object(generator, "list_manifest_records_for_scope", AsyncMock(return_value=records)),
                patch.object(generator, "build_strm_content", side_effect=["expected-content", "new-content"]),
                patch("app.core.cloud115.strm.logger.debug") as debug_mock,
            ):
                result = await generator.sync_strm_files_from_manifest(
                    dir_id="root",
                    output_dir=temp_dir,
                    root_output_dir=temp_dir,
                    base_url="http://localhost:8095",
                )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 1)
        logged_messages = [call.args[0] for call in debug_mock.call_args_list if call.args]
        self.assertTrue(any("strm skipped" in message and "f-1" in message for message in logged_messages))
        self.assertTrue(any("strm written" in message and "f-2" in message for message in logged_messages))

    async def test_get_strm_record_by_file_id_hydrates_missing_play_identity(self):
        row = {
            "id": 8,
            "cloud_type": "115",
            "file_id": "fid-8",
            "archive_dir_id": "cid-8",
            "archive_rel_path": "剧集/Season 1",
            "strm_rel_path": "剧集/Season 1/show.strm",
            "strm_abs_path": "/data/strm_output/剧集/Season 1/show.strm",
            "play_identity": "",
            "task_id": None,
            "strm_path": "/data/strm_output/剧集/Season 1/show.strm",
            "status": "generated",
        }
        db = _AsyncDb(rows=[row])

        with (
            patch("app.core.transfer.strm_manifest.get_local_pickcode", return_value="pickcode-8"),
            patch("app.core.transfer.strm_manifest.get_db_conn", return_value=_AsyncDbContext(db)),
        ):
            record = await get_strm_record_by_file_id(cloud_type="115", file_id="fid-8")

        self.assertEqual(record["play_identity"], "pickcode-8")
        self.assertEqual(db.executed[0][1], ("115", "fid-8"))
        self.assertIn("UPDATE strm_records", db.executed[1][0])

    async def test_get_media_item_link_queries_generic_media_identity(self):
        row = {
            "media_server_type": "emby",
            "media_server_name": "fnos",
            "media_item_id": "item-1",
            "media_source_id": "source-1",
            "cloud_type": "115",
            "file_id": "fid-1",
            "play_identity": "pickcode-1",
            "strm_record_id": 9,
            "source_path": "/mnt/archive/show.strm",
        }
        db = _AsyncDb(rows=[row])

        with patch("app.core.transfer.strm_manifest.get_db_conn", return_value=_AsyncDbContext(db)):
            link = await get_media_item_link(
                media_server_type="emby",
                media_server_name="fnos",
                media_item_id="item-1",
                media_source_id="source-1",
            )

        self.assertEqual(link["play_identity"], "pickcode-1")
        query, params = db.executed[0]
        self.assertIn("FROM media_item_links", query)
        self.assertEqual(params, ("emby", "fnos", "item-1", "source-1"))

    async def test_upsert_media_item_link_uses_generic_unique_key(self):
        db = _AsyncDb(rows=[])

        with patch("app.core.transfer.strm_manifest.get_db_conn", return_value=_AsyncDbContext(db)):
            await upsert_media_item_link(
                media_server_type="jellyfin",
                media_server_name="home",
                media_item_id="item-2",
                media_source_id="source-2",
                cloud_type="115",
                file_id="fid-2",
                play_identity="pickcode-2",
                strm_record_id=10,
                source_path="/media/show.strm",
            )

        query, params = db.executed[0]
        self.assertIn("ON CONFLICT(media_server_type, media_server_name, media_item_id, media_source_id)", query)
        self.assertEqual(
            params,
            ("jellyfin", "home", "item-2", "source-2", "115", "fid-2", "pickcode-2", 10, "/media/show.strm"),
        )
        self.assertEqual(db.executed[-1], ("COMMIT", ()))


if __name__ == "__main__":
    unittest.main()