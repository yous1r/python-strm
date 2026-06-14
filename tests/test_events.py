import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from app.core.notify.bark import BarkNotifier


class _DummyLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


sys.modules.setdefault("loguru", types.SimpleNamespace(logger=_DummyLogger()))


class _DummyClient115:
    async def move_files(self, *args, **kwargs):
        return True

    async def rename_file(self, *args, **kwargs):
        return True


from app.events import (
    EVENT_ROLLBACK_START,
    EVENT_STRM_BATCH_REQUESTED,
    EVENT_STRM_BATCH_REWRITE_REQUESTED,
    EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED,
    EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED,
    EVENT_TRANSFER_BATCH_DONE,
    EVENT_TRANSFER_BATCH_ITEM_DONE,
    EVENT_TRANSFER_BATCH_ITEM_FAILED,
    EVENT_TRANSFER_BATCH_PREPARED,
    EVENT_TRANSFER_BATCH_REQUESTED,
    EventBus,
    event_bus,
    spawn_task,
    task_tracker,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeDbConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        return _FakeCursor([])


class EventBusBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._subscriber_backup = {
            event_type: list(callbacks)
            for event_type, callbacks in event_bus._subscribers.items()
        }
        self._task_backup = dict(task_tracker._tasks)
        event_bus._subscribers.clear()
        task_tracker._tasks.clear()

    async def asyncTearDown(self):
        await asyncio.sleep(0.1)
        event_bus._subscribers = {
            event_type: list(callbacks)
            for event_type, callbacks in self._subscriber_backup.items()
        }
        task_tracker._tasks = dict(self._task_backup)

    async def test_emit_waits_for_async_handler_completion(self):
        bus = EventBus()
        handled = asyncio.Event()
        received = []

        async def handler(value):
            await asyncio.sleep(0.01)
            received.append(value)
            handled.set()

        bus.subscribe("demo", handler)

        await bus.emit("demo", value=42)

        self.assertTrue(handled.is_set())
        self.assertEqual(received, [42])

    async def test_emit_background_returns_tracked_task_id(self):
        bus = EventBus()
        handled = asyncio.Event()

        async def handler():
            handled.set()

        bus.subscribe("demo", handler)

        task_id = bus.emit_background("demo", name="demo_event")
        await asyncio.wait_for(handled.wait(), timeout=1)

        tasks = await task_tracker.get_active()
        tracked_ids = {task["task_id"] for task in tasks}
        self.assertIn(task_id, tracked_ids)

    async def test_emit_background_does_not_block_caller(self):
        bus = EventBus()
        handled = asyncio.Event()

        async def handler():
            await asyncio.sleep(0.05)
            handled.set()

        bus.subscribe("demo", handler)

        started_at = asyncio.get_running_loop().time()
        task_id = bus.emit_background("demo")
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 0.02)
        self.assertIsInstance(task_id, str)
        self.assertFalse(handled.is_set())

        await asyncio.wait_for(handled.wait(), timeout=1)

    async def test_emit_logs_when_handler_starts_processing_event(self):
        bus = EventBus()

        async def handler(value):
            return value

        bus.subscribe("demo", handler)

        with patch("app.events.logger.debug") as debug_mock:
            await bus.emit("demo", value=42)

        logged_messages = [call.args[0] for call in debug_mock.call_args_list if call.args]
        self.assertTrue(any("emit demo" in message for message in logged_messages))
        self.assertTrue(any("handle demo" in message for message in logged_messages))

    async def test_spawn_task_returns_tracked_task_id(self):
        task_id = spawn_task(asyncio.sleep(0.05), name="tracked_sleep")

        await asyncio.sleep(0.01)

        tasks = await task_tracker.get_active()
        tracked_ids = {task["task_id"] for task in tasks}
        self.assertIn(task_id, tracked_ids)

    async def test_spawn_task_records_error_without_unretrieved_task_exception(self):
        observed_contexts = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: observed_contexts.append(context))

        async def boom():
            raise RuntimeError("database is locked")

        try:
            task_id = spawn_task(boom(), name="boom")
            await asyncio.sleep(0.05)
        finally:
            loop.set_exception_handler(previous_handler)

        tasks = await task_tracker.get_active()
        task = next(item for item in tasks if item["task_id"] == task_id)
        self.assertEqual(task["status"], "error")
        self.assertEqual(task["error"], "database is locked")
        self.assertEqual(observed_contexts, [])

    async def test_rollback_task_does_not_reemit_start_event(self):
        observed = []

        def on_start(task_id, **kwargs):
            observed.append(task_id)

        event_bus.subscribe(EVENT_ROLLBACK_START, on_start)

        rollback_spec = importlib.util.spec_from_file_location(
            "test_rollback_module",
            Path(__file__).resolve().parents[1] / "app" / "core" / "transfer" / "rollback.py",
        )
        rollback_module = importlib.util.module_from_spec(rollback_spec)
        assert rollback_spec and rollback_spec.loader

        with patch.dict(
            sys.modules,
            {
                "app.core.cloud115.client": types.SimpleNamespace(client_115=_DummyClient115()),
                "app.database": types.SimpleNamespace(get_db_conn=lambda: None),
            },
        ):
            rollback_spec.loader.exec_module(rollback_module)

            with patch.object(rollback_module, "get_db_conn", return_value=_FakeDbConnection()):
                await rollback_module.rollback_task("task-1")

        self.assertEqual(observed, [])


class BarkNotifierPayloadTests(unittest.TestCase):
    def test_build_payload_maps_message_type_to_bark_fields(self):
        payload = BarkNotifier()._build_payload(
            title="title",
            content="content",
            message_type="error",
            group="transfer-batch:demo",
            subtitle="失败 1/3",
            is_archive=True,
        )

        self.assertEqual(payload["title"], "title")
        self.assertEqual(payload["body"], "content")
        self.assertEqual(payload["level"], "timeSensitive")
        self.assertEqual(payload["sound"], "alarm")
        self.assertEqual(payload["group"], "transfer-batch:demo")
        self.assertEqual(payload["subtitle"], "失败 1/3")
        self.assertEqual(payload["isArchive"], "1")


class BatchEventConstantTests(unittest.TestCase):
    def test_batch_event_names_are_stable(self):
        self.assertEqual(EVENT_TRANSFER_BATCH_REQUESTED, "transfer_batch_requested")
        self.assertEqual(EVENT_TRANSFER_BATCH_PREPARED, "transfer_batch_prepared")
        self.assertEqual(EVENT_TRANSFER_BATCH_ITEM_DONE, "transfer_batch_item_done")
        self.assertEqual(EVENT_TRANSFER_BATCH_ITEM_FAILED, "transfer_batch_item_failed")
        self.assertEqual(EVENT_TRANSFER_BATCH_DONE, "transfer_batch_done")
        self.assertEqual(EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED, "transfer_batch_db_sync_requested")
        self.assertEqual(EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED, "transfer_batch_db_sync_completed")
        self.assertEqual(EVENT_STRM_BATCH_REQUESTED, "strm_batch_requested")
        self.assertEqual(EVENT_STRM_BATCH_REWRITE_REQUESTED, "strm_batch_rewrite_requested")


class StrmBatchPayloadTests(unittest.TestCase):
    def test_strm_batch_requested_payload_contains_archive_placement(self):
        payload = {
            "task_id": "task-1",
            "cloud_type": "115",
            "archive_dir_id": "cid-9",
            "archive_rel_path": "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            "strm_rel_dir": "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            "files": [{"file_id": "fid-1", "file_name": "灵魂摆渡·十年.2026.S01E05.mkv"}],
        }

        self.assertEqual(payload["archive_dir_id"], "cid-9")
        self.assertEqual(EVENT_STRM_BATCH_REQUESTED, "strm_batch_requested")


class EventBusBatchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_preserves_batch_event_order(self):
        bus = EventBus()
        observed = []

        async def on_requested(task_id, **kwargs):
            observed.append(("requested", task_id))

        async def on_done(task_id, **kwargs):
            observed.append(("done", task_id))

        bus.subscribe(EVENT_TRANSFER_BATCH_REQUESTED, on_requested)
        bus.subscribe(EVENT_TRANSFER_BATCH_DONE, on_done)

        await bus.emit(EVENT_TRANSFER_BATCH_REQUESTED, task_id="batch-1")
        await bus.emit(EVENT_TRANSFER_BATCH_DONE, task_id="batch-1")

        self.assertEqual(observed, [("requested", "batch-1"), ("done", "batch-1")])


if __name__ == "__main__":
    unittest.main()

# http://git.8781969.xyz:8080/
# http://git.8781969.xyz:8080
# http://10.0.0.142:8317/v1
# gpt5.4
