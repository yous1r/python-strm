import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config import AppConfig, MonitorConfig, TelegramConfig


class StartupBootstrapTests(unittest.IsolatedAsyncioTestCase):
    def test_app_config_includes_startup_pipeline_defaults(self):
        config = AppConfig()

        self.assertFalse(config.monitor.startup_pipeline.enabled)
        self.assertTrue(config.monitor.startup_pipeline.run_db_sync)
        self.assertTrue(config.monitor.startup_pipeline.run_telegram_sync)
        self.assertTrue(config.monitor.startup_pipeline.run_strm_sync)
        self.assertEqual(config.monitor.telegram.startup_sync, "latest")

    async def test_run_startup_pipeline_executes_enabled_steps_in_order(self):
        from app.services.startup_bootstrap_service import run_startup_pipeline

        calls = []

        async def fake_db_sync():
            calls.append("db")
            return {"status": "ok"}

        def fake_build_request_for_startup():
            calls.append("telegram:build")
            return SimpleNamespace(source="startup")

        def fake_queue_history_sync_request(request, *, name):
            calls.append(("telegram:queue", name, request.source))
            return {"status": "success", "queued": True, "task_id": "tg-1"}

        async def fake_sync_task(force=False):
            calls.append(("strm", force))
            return {"status": "ok"}

        config = AppConfig(
            monitor=MonitorConfig(
                telegram=TelegramConfig(
                    enabled=True,
                    api_id="1",
                    api_hash="2",
                    channels=["@demo"],
                    startup_sync="incremental",
                ),
                startup_pipeline={
                    "enabled": True,
                    "run_db_sync": True,
                    "run_telegram_sync": True,
                    "run_strm_sync": True,
                },
            )
        )

        with patch("app.services.startup_bootstrap_service.get_config", lambda: config), \
             patch("app.services.startup_bootstrap_service.sync_all_configured", fake_db_sync), \
             patch("app.services.startup_bootstrap_service.telegram_history_sync_service.build_request_for_startup", fake_build_request_for_startup), \
             patch("app.services.startup_bootstrap_service.telegram_history_sync_service.queue_history_sync_request", fake_queue_history_sync_request), \
             patch("app.services.startup_bootstrap_service.sync_engine.run_sync_task", fake_sync_task):
            result = await run_startup_pipeline()

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, ["db", "telegram:build", ("telegram:queue", "telegram_history_sync:startup", "startup"), ("strm", False)])

    async def test_run_startup_pipeline_skips_disabled_telegram_step(self):
        from app.services.startup_bootstrap_service import run_startup_pipeline

        build_request_mock = Mock()
        queue_request_mock = Mock()
        strm_sync = AsyncMock(return_value={"status": "ok"})

        config = AppConfig(
            monitor=MonitorConfig(
                telegram=TelegramConfig(
                    enabled=True,
                    api_id="1",
                    api_hash="2",
                    channels=["@demo"],
                    startup_sync="incremental",
                ),
                startup_pipeline={
                    "enabled": True,
                    "run_db_sync": False,
                    "run_telegram_sync": False,
                    "run_strm_sync": True,
                },
            )
        )

        with patch("app.services.startup_bootstrap_service.get_config", lambda: config), \
             patch("app.services.startup_bootstrap_service.telegram_history_sync_service.build_request_for_startup", build_request_mock), \
             patch("app.services.startup_bootstrap_service.telegram_history_sync_service.queue_history_sync_request", queue_request_mock), \
             patch("app.services.startup_bootstrap_service.sync_engine.run_sync_task", strm_sync):
            result = await run_startup_pipeline()

        self.assertEqual(result["status"], "success")
        build_request_mock.assert_not_called()
        queue_request_mock.assert_not_called()
        strm_sync.assert_awaited_once_with(force=False)

    async def test_queue_startup_workflows_uses_unique_background_tasks(self):
        from app.main import queue_startup_workflows

        calls = []

        async def fake_spawn_unique(key, factory, *, name, pool):
            calls.append({"key": key, "name": name, "pool": pool, "factory": factory})
            return SimpleNamespace(task_id=f"task-{key}", queued=True)

        config = AppConfig(
            monitor=MonitorConfig(
                telegram=TelegramConfig(enabled=True),
                startup_pipeline={"enabled": True},
            )
        )

        with patch("app.main.background_task_coordinator.spawn_unique", fake_spawn_unique):
            result = await queue_startup_workflows(config)

        self.assertEqual(
            [item["key"] for item in calls],
            ["startup_pipeline", "telegram_monitor", "standalone_proxy"],
        )
        self.assertEqual(result["startup_pipeline"].task_id, "task-startup_pipeline")
        self.assertEqual(result["telegram_monitor"].task_id, "task-telegram_monitor")
        self.assertEqual(result["standalone_proxy"].task_id, "task-standalone_proxy")

    async def test_lifespan_registers_transfer_pipeline_events(self):
        from app.main import lifespan

        config = AppConfig()

        with patch("app.main.init_db", AsyncMock()), \
             patch("app.main.start_scheduler"), \
             patch("app.main.init_cloud115_full_sync_events"), \
             patch("app.main.init_telegram_history_sync_events"), \
             patch("app.main.init_transfer_pipeline") as mocked_init_transfer, \
             patch("app.main.add_job"), \
             patch("app.main.telegram_background_sync_service.configure_scheduled_sync_job"), \
             patch("app.main.get_config", return_value=config), \
             patch("app.main.queue_startup_workflows", AsyncMock()), \
             patch("app.main.stop_standalone_proxy", AsyncMock()), \
             patch("app.main.telegram_monitor.stop", AsyncMock()), \
             patch("app.main.stop_scheduler"):
            async with lifespan(SimpleNamespace()):
                pass

        mocked_init_transfer.assert_called_once()


class TelegramMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_does_not_schedule_startup_sync(self):
        from app.core.monitor.telegram import TelegramMonitor

        class FakeClient:
            def on(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            async def connect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def start(self, *args, **kwargs):
                return None

            async def disconnect(self):
                return None

        fake_config = SimpleNamespace(
            enabled=True,
            api_id="1",
            api_hash="2",
            proxy="",
            channels=["@demo"],
            keywords=["电影"],
            startup_sync="latest",
            bot_token="",
        )
        wrapped_config = SimpleNamespace(monitor=SimpleNamespace(telegram=fake_config))
        create_task_mock = AsyncMock()
        monitor = TelegramMonitor()
        monitor.config = fake_config

        with patch("app.core.monitor.telegram.get_config", lambda: wrapped_config), \
             patch("app.core.monitor.telegram.build_telegram_client", lambda *args, **kwargs: FakeClient()), \
             patch("app.core.monitor.telegram.parse_channels", lambda channels: channels), \
             patch("app.services.telegram_service.sync_configured_channels", AsyncMock()), \
             patch("asyncio.create_task", create_task_mock):
            await monitor.start()

        create_task_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
