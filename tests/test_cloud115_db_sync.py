import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app.core.cloud115.db_sync import (
    handle_cloud115_db_sync_completed,
    sync_all_configured,
)
from app.events import EVENT_CLOUD115_DB_SYNC_COMPLETED


class Cloud115DbSyncEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_all_configured_emits_sync_completed_for_sync_dirs(self):
        config = SimpleNamespace(
            cloud115=SimpleNamespace(
                sync_dirs=[
                    SimpleNamespace(dir_id="100", name="影视"),
                    SimpleNamespace(dir_id="200", name="动漫"),
                ]
            ),
            transfer=SimpleNamespace(enabled=False, temp_dir_id="", archive_dir_id=""),
        )
        emit_mock = AsyncMock()

        with (
            patch("app.core.cloud115.db_sync.get_config", return_value=config),
            patch("app.core.cloud115.db_sync.sync_directory", side_effect=[0, 5]),
            patch("app.core.cloud115.db_sync.event_bus.emit", emit_mock),
        ):
            result = await sync_all_configured()

        self.assertEqual(result, {"影视": 0, "动漫": 5})
        emit_mock.assert_has_awaits(
            [
                call(
                    EVENT_CLOUD115_DB_SYNC_COMPLETED,
                    dir_id="100",
                    dir_name="影视",
                    recursive=True,
                    count=0,
                    source="sync_all_configured",
                ),
                call(
                    EVENT_CLOUD115_DB_SYNC_COMPLETED,
                    dir_id="200",
                    dir_name="动漫",
                    recursive=True,
                    count=5,
                    source="sync_all_configured",
                ),
            ]
        )

    async def test_sync_completed_handler_refreshes_strm_for_synced_dir(self):
        config = SimpleNamespace(
            strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095")
        )
        sync_strm = AsyncMock(return_value={"scanned": 5, "updated": 2, "skipped": 3, "failed": 0})
        plugin = SimpleNamespace(
            strm_generator=SimpleNamespace(sync_strm_files_from_manifest=sync_strm)
        )

        with (
            patch("app.core.cloud115.db_sync.get_config", return_value=config),
            patch(
                "app.core.cloud.get_cloud_plugin",
                return_value=plugin,
            ),
        ):
            await handle_cloud115_db_sync_completed(dir_id="300", dir_name="综艺", count=0)

        sync_strm.assert_awaited_once_with(
            dir_id="300",
            output_dir="strm_output",
            root_output_dir="strm_output",
            base_url="http://localhost:8095",
            archive_root="",
        )

    async def test_sync_all_configured_only_includes_archive_dir_for_transfer_pipeline(self):
        config = SimpleNamespace(
            cloud115=SimpleNamespace(sync_dirs=[]),
            transfer=SimpleNamespace(enabled=True, archive_dir_id="archive-root", temp_dir_id="legacy-temp"),
        )

        with (
            patch("app.core.cloud115.db_sync.get_config", return_value=config),
            patch("app.core.cloud115.db_sync.sync_directory", side_effect=[7]) as mocked_sync,
            patch("app.core.cloud115.db_sync.event_bus.emit", AsyncMock()),
        ):
            result = await sync_all_configured()

        self.assertEqual(result, {"archive_dir": 7})
        mocked_sync.assert_awaited_once_with("archive-root", "archive_dir")


if __name__ == "__main__":
    unittest.main()
