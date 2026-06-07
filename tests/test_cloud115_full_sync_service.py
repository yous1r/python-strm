import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.cloud115_full_sync_service import Cloud115FullSyncService


class Cloud115FullSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_db_sync_step_returns_structured_dir_results(self):
        service = Cloud115FullSyncService()
        dirs = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "recursive": True,
                "output_dir": "strm_output/影视",
                "role": "sync_dir",
                "strm_enabled": True,
            }
        ]

        with patch(
            "app.services.cloud115_full_sync_service.sync_directory",
            new=AsyncMock(return_value=7),
        ):
            results = await service.run_db_sync_step(dirs)

        self.assertEqual(results[0]["dir_id"], "100")
        self.assertEqual(results[0]["dir_name"], "影视")
        self.assertEqual(results[0]["count"], 7)

    async def test_run_manifest_refresh_step_returns_cleanup_stats(self):
        service = Cloud115FullSyncService()
        config = SimpleNamespace(strm=SimpleNamespace(output_dir="strm_output"))
        dirs = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "recursive": True,
                "output_dir": "strm_output/影视",
                "role": "sync_dir",
                "strm_enabled": True,
            },
            {
                "dir_id": "200",
                "dir_name": "temp_dir",
                "recursive": True,
                "output_dir": "",
                "role": "temp_dir",
                "strm_enabled": False,
            },
        ]

        with (
            patch("app.services.cloud115_full_sync_service.get_config", return_value=config),
            patch(
                "app.services.cloud115_full_sync_service.generator_115.cleanup_invalid_strm_records",
                new=AsyncMock(return_value={"records": 2, "files": 1}),
            ) as cleanup_mock,
        ):
            result = await service.run_manifest_refresh_step(dirs)

        self.assertEqual(result["results"], [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "output_dir": "strm_output/影视",
                "cleaned_records": 2,
                "deleted_strm_files": 1,
            }
        ])
        self.assertEqual(len(result["dirs"]), 1)
        cleanup_mock.assert_awaited_once()

    async def test_run_strm_refresh_step_returns_generation_stats(self):
        service = Cloud115FullSyncService()
        config = SimpleNamespace(
            strm=SimpleNamespace(output_dir="strm_output", base_url="http://localhost:8095")
        )
        dirs = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "recursive": True,
                "output_dir": "strm_output/影视",
                "role": "sync_dir",
                "strm_enabled": True,
            }
        ]

        with (
            patch("app.services.cloud115_full_sync_service.get_config", return_value=config),
            patch(
                "app.services.cloud115_full_sync_service.generator_115.batch_generate",
                new=AsyncMock(return_value=["a.strm", "b.strm"]),
            ) as generate_mock,
        ):
            results = await service.run_strm_refresh_step(dirs)

        self.assertEqual(results[0]["generated_count"], 2)
        self.assertEqual(results[0]["generated_files"], ["a.strm", "b.strm"])
        generate_mock.assert_awaited_once_with(
            dir_id="100",
            output_dir="strm_output/影视",
            base_url="http://localhost:8095",
            recursive=True,
            root_output_dir="strm_output",
            force=False,
            cleanup_invalid=False,
        )

    async def test_pipeline_stops_when_manifest_step_fails(self):
        service = Cloud115FullSyncService()
        service._tasks["task-1"] = {
            "task_id": "task-1",
            "status": "running",
            "current_stage": "queued",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": None,
            "dirs": [],
            "db_sync_results": [],
            "manifest_results": [],
            "strm_results": [],
            "stats": {
                "db_sync_rows": 0,
                "cleaned_records": 0,
                "deleted_strm_files": 0,
                "generated_strm_files": 0,
            },
            "error": "",
        }
        dirs = [{"dir_id": "100", "dir_name": "影视", "output_dir": "strm_output/影视", "strm_enabled": True}]

        with (
            patch.object(service, "run_manifest_refresh_step", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch("app.services.cloud115_full_sync_service.event_bus.emit", new=AsyncMock()) as emit_mock,
        ):
            await service.handle_db_sync_finished(task_id="task-1", dirs=dirs)

        task = await service.get_task("task-1")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["current_stage"], "manifest_refresh")
        self.assertIn("boom", task["error"])
        emit_mock.assert_not_awaited()
