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


sys.modules.setdefault(
    "app.core.cloud115.client",
    types.SimpleNamespace(client_115=_DummyClient115()),
)
sys.modules.setdefault(
    "app.database",
    types.SimpleNamespace(get_db_conn=lambda: None),
)


_ROLLBACK_SPEC = importlib.util.spec_from_file_location(
    "test_rollback_module",
    Path(__file__).resolve().parents[1] / "app" / "core" / "transfer" / "rollback.py",
)
rollback_module = importlib.util.module_from_spec(_ROLLBACK_SPEC)
assert _ROLLBACK_SPEC and _ROLLBACK_SPEC.loader
_ROLLBACK_SPEC.loader.exec_module(rollback_module)

from app.events import (
    EVENT_ROLLBACK_START,
    EVENT_STRM_BATCH_REQUESTED,
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

    async def test_spawn_task_returns_tracked_task_id(self):
        task_id = spawn_task(asyncio.sleep(0.05), name="tracked_sleep")

        await asyncio.sleep(0.01)

        tasks = await task_tracker.get_active()
        tracked_ids = {task["task_id"] for task in tasks}
        self.assertIn(task_id, tracked_ids)

    async def test_rollback_task_does_not_reemit_start_event(self):
        observed = []

        def on_start(task_id, **kwargs):
            observed.append(task_id)

        event_bus.subscribe(EVENT_ROLLBACK_START, on_start)

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