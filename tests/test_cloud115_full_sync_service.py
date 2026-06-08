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
        config = SimpleNamespace(strm=SimpleNamespace(output_dir="strm_output", base_url=""))
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
                "app.services.cloud115_full_sync_service.generator_115.sync_manifest_records",
                new=AsyncMock(
                    return_value={
                        "scanned": 5,
                        "created": 2,
                        "updated": 1,
                        "unchanged": 2,
                        "deleted_records": 2,
                        "deleted_files": 1,
                        "changed": True,
                    }
                ),
            ) as manifest_mock,
        ):
            result = await service.run_manifest_refresh_step(dirs)

        self.assertEqual(result["results"], [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "output_dir": "strm_output/影视",
                "scanned_records": 5,
                "created_records": 2,
                "updated_records": 1,
                "unchanged_records": 2,
                "cleaned_records": 2,
                "deleted_strm_files": 1,
                "changed": True,
            }
        ])
        self.assertEqual(len(result["dirs"]), 1)
        self.assertTrue(result["has_changes"])
        manifest_mock.assert_awaited_once_with(
            dir_id="100",
            output_dir="strm_output/影视",
            base_url="",
            recursive=True,
            root_output_dir="strm_output",
        )

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
                "app.services.cloud115_full_sync_service.generator_115.sync_strm_files_from_manifest",
                new=AsyncMock(return_value={"scanned": 4, "updated": 2, "skipped": 2, "failed": 0, "files": ["a.strm", "b.strm"]}),
            ) as generate_mock,
        ):
            results = await service.run_strm_refresh_step(dirs)

        self.assertEqual(results[0]["generated_count"], 2)
        self.assertEqual(results[0]["generated_files"], ["a.strm", "b.strm"])
        self.assertEqual(results[0]["scanned_count"], 4)
        self.assertEqual(results[0]["skipped_count"], 2)
        self.assertEqual(results[0]["failed_count"], 0)
        generate_mock.assert_awaited_once_with(
            dir_id="100",
            output_dir="strm_output/影视",
            root_output_dir="strm_output",
            base_url="http://localhost:8095",
        )

    async def test_pipeline_stops_when_manifest_step_fails(self):
        service = Cloud115FullSyncService()
        service._tasks["task-1"] = {
            "task_id": "task-1",
            "status": "running",
            "current_stage": "queued",
            "skip_reason": "",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": None,
            "dirs": [],
            "db_sync_results": [],
            "manifest_results": [],
            "strm_results": [],
            "media_link_results": [],
            "stats": {
                "db_sync_rows": 0,
                "cleaned_records": 0,
                "deleted_strm_files": 0,
                "generated_strm_files": 0,
                "media_links_scanned": 0,
                "media_links_linked": 0,
                "media_links_skipped": 0,
                "media_links_failed": 0,
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

    async def test_handle_full_sync_requested_skips_followup_when_no_dir_changed(self):
        service = Cloud115FullSyncService()
        service._tasks["task-1"] = {
            "task_id": "task-1",
            "source": "debug",
            "status": "running",
            "current_stage": "queued",
            "skip_reason": "",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": None,
            "dirs": [],
            "db_sync_results": [],
            "manifest_results": [],
            "strm_results": [],
            "media_link_results": [],
            "stats": {
                "db_sync_rows": 0,
                "cleaned_records": 0,
                "deleted_strm_files": 0,
                "generated_strm_files": 0,
                "media_links_scanned": 0,
                "media_links_linked": 0,
                "media_links_skipped": 0,
                "media_links_failed": 0,
            },
            "error": "",
        }
        db_sync_results = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "recursive": True,
                "output_dir": "strm_output/影视",
                "role": "sync_dir",
                "strm_enabled": True,
                "count": 0,
            },
            {
                "dir_id": "200",
                "dir_name": "temp_dir",
                "recursive": True,
                "output_dir": "",
                "role": "temp_dir",
                "strm_enabled": False,
                "count": 0,
            },
        ]

        with (
            patch.object(service, "run_db_sync_step", new=AsyncMock(return_value=db_sync_results)),
            patch("app.services.cloud115_full_sync_service.event_bus.emit", new=AsyncMock()) as emit_mock,
        ):
            await service.handle_full_sync_requested(task_id="task-1", source="debug")

        task = await service.get_task("task-1")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["current_stage"], "completed")
        self.assertIn("跳过", task["skip_reason"])
        self.assertEqual(task["stats"]["db_sync_rows"], 0)
        emit_mock.assert_awaited_once()

    def test_select_changed_strm_dirs_filters_zero_count_dirs(self):
        service = Cloud115FullSyncService()
        dirs = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "output_dir": "strm_output/影视",
                "strm_enabled": True,
                "count": 0,
            },
            {
                "dir_id": "101",
                "dir_name": "动漫",
                "output_dir": "strm_output/动漫",
                "strm_enabled": True,
                "count": 4,
            },
            {
                "dir_id": "200",
                "dir_name": "temp_dir",
                "output_dir": "",
                "strm_enabled": False,
                "count": 9,
            },
        ]

        changed = service._select_changed_strm_dirs(dirs)

        self.assertEqual(changed, [dirs[1]])

    async def test_handle_db_sync_finished_completes_when_manifest_has_no_changes(self):
        service = Cloud115FullSyncService()
        service._tasks["task-1"] = {
            "task_id": "task-1",
            "source": "debug",
            "status": "running",
            "current_stage": "manifest_refresh",
            "skip_reason": "",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": None,
            "dirs": [],
            "db_sync_results": [],
            "manifest_results": [],
            "strm_results": [],
            "media_link_results": [],
            "stats": {
                "db_sync_rows": 0,
                "cleaned_records": 0,
                "deleted_strm_files": 0,
                "generated_strm_files": 0,
                "media_links_scanned": 0,
                "media_links_linked": 0,
                "media_links_skipped": 0,
                "media_links_failed": 0,
            },
            "error": "",
        }

        manifest_result = {
            "results": [
                {
                    "dir_id": "100",
                    "dir_name": "影视",
                    "output_dir": "strm_output/影视",
                    "scanned_records": 3,
                    "created_records": 0,
                    "updated_records": 0,
                    "unchanged_records": 3,
                    "cleaned_records": 0,
                    "deleted_strm_files": 0,
                    "changed": False,
                }
            ],
            "dirs": [],
            "has_changes": False,
        }

        with (
            patch.object(service, "run_manifest_refresh_step", new=AsyncMock(return_value=manifest_result)),
            patch("app.services.cloud115_full_sync_service.event_bus.emit", new=AsyncMock()) as emit_mock,
        ):
            await service.handle_db_sync_finished(task_id="task-1", dirs=[{"dir_id": "100", "dir_name": "影视", "output_dir": "strm_output/影视", "strm_enabled": True}], source="debug")

        task = await service.get_task("task-1")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["current_stage"], "completed")
        self.assertIn("未发现差异", task["skip_reason"])
        emit_mock.assert_awaited_once()

    async def test_handle_manifest_refresh_finished_runs_parallel_refresh(self):
        service = Cloud115FullSyncService()
        service._tasks["task-1"] = {
            "task_id": "task-1",
            "source": "debug",
            "status": "running",
            "current_stage": "manifest_refresh",
            "skip_reason": "",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": None,
            "dirs": [],
            "db_sync_results": [],
            "manifest_results": [],
            "strm_results": [],
            "media_link_results": [],
            "stats": {
                "db_sync_rows": 0,
                "cleaned_records": 0,
                "deleted_strm_files": 0,
                "generated_strm_files": 0,
                "media_links_scanned": 0,
                "media_links_linked": 0,
                "media_links_skipped": 0,
                "media_links_failed": 0,
            },
            "error": "",
        }

        media_link_result = {
            "status": "success",
            "instance_count": 1,
            "results": [
                {
                    "media_server_type": "emby",
                    "media_server_name": "emby-1",
                    "scanned": 10,
                    "linked": 4,
                    "skipped": 6,
                    "failed": 0,
                }
            ],
        }
        strm_results = [
            {
                "dir_id": "100",
                "dir_name": "影视",
                "output_dir": "strm_output/影视",
                "generated_count": 2,
                "generated_files": ["a.strm", "b.strm"],
                "scanned_count": 4,
                "skipped_count": 2,
                "failed_count": 0,
            }
        ]

        with (
            patch.object(service, "run_strm_refresh_step", new=AsyncMock(return_value=strm_results)),
            patch.object(service, "run_media_links_refresh_step", new=AsyncMock(return_value=media_link_result)),
            patch("app.services.cloud115_full_sync_service.event_bus.emit", new=AsyncMock()) as emit_mock,
        ):
            await service.handle_manifest_refresh_finished(
                task_id="task-1",
                dirs=[{"dir_id": "100", "dir_name": "影视", "output_dir": "strm_output/影视", "strm_enabled": True}],
                source="debug",
            )

        task = await service.get_task("task-1")
        self.assertIsNotNone(task)
        self.assertEqual(task["current_stage"], "parallel_refresh")
        self.assertEqual(task["strm_results"], strm_results)
        self.assertEqual(task["media_link_results"], media_link_result["results"])
        self.assertEqual(task["stats"]["generated_strm_files"], 2)
        self.assertEqual(task["stats"]["media_links_scanned"], 10)
        self.assertEqual(task["stats"]["media_links_linked"], 4)
        emit_mock.assert_awaited_once()

    async def test_run_media_links_refresh_step_skips_without_enabled_instances(self):
        service = Cloud115FullSyncService()
        config = SimpleNamespace(
            emby=SimpleNamespace(
                proxy=SimpleNamespace(
                    instances=[]
                )
            )
        )

        with patch("app.services.cloud115_full_sync_service.get_config", return_value=config):
            result = await service.run_media_links_refresh_step()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["instance_count"], 0)
        self.assertIn("跳过", result["reason"])

    async def test_run_media_links_refresh_step_skips_when_preheat_disabled(self):
        service = Cloud115FullSyncService()
        config = SimpleNamespace(
            emby=SimpleNamespace(
                proxy=SimpleNamespace(
                    instances=[SimpleNamespace(url="http://emby.local", name="emby-1")],
                    preheat_on_full_sync=False,
                )
            )
        )

        with (
            patch("app.services.cloud115_full_sync_service.get_config", return_value=config),
            patch("app.services.cloud115_full_sync_service.preheat_media_item_links", new=AsyncMock()) as preheat_mock,
        ):
            result = await service.run_media_links_refresh_step()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["instance_count"], 1)
        self.assertIn("已禁用", result["reason"])
        preheat_mock.assert_not_awaited()
