"""事件总线 + 后台任务追踪器"""
import asyncio
import inspect
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List

from loguru import logger


class TaskTracker:
    """追踪所有 asyncio.create_task() 创建的后台任务，提供可观测性"""

    def __init__(self):
        self._tasks: Dict[str, dict] = {}  # task_id -> {name, type, created_at, status, error}
        self._lock = asyncio.Lock()

    async def spawn(self, coro: Awaitable[Any], name: str = "", task_type: str = "async") -> str:
        """
        创建并追踪一个后台任务。
        task_type: "async" (协程) 或 "thread" (asyncio.to_thread)
        返回 task_id
        """
        task_id = str(uuid.uuid4())[:8]
        now = time.time()

        async with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "name": name or getattr(coro, "__name__", "unknown"),
                "type": task_type,
                "created_at": now,
                "status": "running",
                "error": None,
            }

        # 包装协程，完成后自动更新状态
        async def _wrapper():
            try:
                result = await coro
                async with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id]["status"] = "done"
                        self._tasks[task_id]["duration"] = time.time() - now
                logger.debug(f"[TaskTracker] ✓ {task_id} ({name}): done")
                return result
            except Exception as e:
                async with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id]["status"] = "error"
                        self._tasks[task_id]["error"] = str(e)[:200]
                        self._tasks[task_id]["duration"] = time.time() - now
                logger.error(f"[TaskTracker] ✗ {task_id} ({name}): {e}")
                raise

        asyncio.create_task(_wrapper())
        return task_id

    async def create_task(
        self,
        coro: Awaitable[Any],
        *,
        name: str = "",
        task_type: str = "async",
    ) -> tuple[str, asyncio.Task]:
        """创建已登记的后台任务，并返回 task_id 与 task 对象。"""
        task_id = str(uuid.uuid4())[:8]
        now = time.time()

        async with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "name": name or getattr(coro, "__name__", "unknown"),
                "type": task_type,
                "created_at": now,
                "status": "running",
                "error": None,
            }

        async def _wrapper():
            try:
                result = await coro
                async with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id]["status"] = "done"
                        self._tasks[task_id]["duration"] = time.time() - now
                logger.debug(f"[TaskTracker] ✓ {task_id} ({name}): done")
                return result
            except Exception as e:
                async with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id]["status"] = "error"
                        self._tasks[task_id]["error"] = str(e)[:200]
                        self._tasks[task_id]["duration"] = time.time() - now
                logger.error(f"[TaskTracker] ✗ {task_id} ({name}): {e}")
                raise

        task = asyncio.create_task(_wrapper())
        return task_id, task

    async def track_to_thread(self, func, *args, name: str = "", **kwargs) -> str:
        """追踪 asyncio.to_thread 调用"""
        task_id = str(uuid.uuid4())[:8]
        now = time.time()

        async with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "name": name or getattr(func, "__name__", "unknown"),
                "type": "thread",
                "created_at": now,
                "status": "running",
                "error": None,
            }

        try:
            result = await asyncio.to_thread(func, *args, **kwargs)
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "done"
                    self._tasks[task_id]["duration"] = time.time() - now
            return result
        except Exception as e:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "error"
                    self._tasks[task_id]["error"] = str(e)[:200]
                    self._tasks[task_id]["duration"] = time.time() - now
            raise

    async def get_active(self) -> List[dict]:
        """获取所有活跃/最近完成的任务（保留最近60秒）"""
        now = time.time()
        cleanup = []
        result = []
        async with self._lock:
            for tid, t in self._tasks.items():
                if t["status"] == "running":
                    result.append(t)
                elif now - t["created_at"] < 60:
                    result.append(t)
                else:
                    cleanup.append(tid)
            for tid in cleanup:
                del self._tasks[tid]
        # 按创建时间倒序
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    async def get_summary(self) -> dict:
        """获取任务汇总"""
        async with self._lock:
            running = sum(1 for t in self._tasks.values() if t["status"] == "running")
            done = sum(1 for t in self._tasks.values() if t["status"] == "done")
            error = sum(1 for t in self._tasks.values() if t["status"] == "error")
        return {
            "total_tracked": len(self._tasks),
            "running": running,
            "done": done,
            "error": error,
        }


# 全局任务追踪器
task_tracker = TaskTracker()


def spawn_task(coro: Awaitable[Any], name: str = "", task_type: str = "async") -> str:
    """
    创建并追踪后台任务。替代裸 asyncio.create_task()。
    返回 task_id 供后续查询。
    """
    task_id = str(uuid.uuid4())[:8]

    async def _deferred():
        async with task_tracker._lock:
            task_tracker._tasks[task_id] = {
                "task_id": task_id,
                "name": name or getattr(coro, "__name__", "unknown"),
                "type": task_type,
                "created_at": time.time(),
                "status": "running",
                "error": None,
            }

        started_at = task_tracker._tasks[task_id]["created_at"]
        try:
            result = await coro
            async with task_tracker._lock:
                if task_id in task_tracker._tasks:
                    task_tracker._tasks[task_id]["status"] = "done"
                    task_tracker._tasks[task_id]["duration"] = time.time() - started_at
            logger.debug(f"[TaskTracker] ✓ {task_id} ({name}): done")
            return result
        except Exception as e:
            async with task_tracker._lock:
                if task_id in task_tracker._tasks:
                    task_tracker._tasks[task_id]["status"] = "error"
                    task_tracker._tasks[task_id]["error"] = str(e)[:200]
                    task_tracker._tasks[task_id]["duration"] = time.time() - started_at
            logger.error(f"[TaskTracker] ✗ {task_id} ({name}): {e}")
            raise

    asyncio.create_task(_deferred())
    return task_id


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed to {event_type}: {getattr(callback, '__name__', 'lambda')}")

    def unsubscribe(self, event_type: str, callback: Callable):
        if event_type in self._subscribers and callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    async def emit(self, event_type: str, **kwargs):
        if event_type not in self._subscribers:
            return
        callbacks = list(self._subscribers[event_type])
        logger.debug(f"[EventBus] emit {event_type} → {len(callbacks)} handler(s)")
        tasks = []
        for callback in callbacks:
            name = f"event:{event_type}:{getattr(callback, '__name__', str(callback))}"
            task_id, task = await task_tracker.create_task(
                self._safe_call(event_type, callback, **kwargs),
                name=name,
                task_type="async",
            )
            tasks.append((task_id, task))
        if tasks:
            await asyncio.gather(*(task for _, task in tasks))

    def emit_background(self, event_type: str, *, name: str = "", **kwargs) -> str:
        """后台发布事件，返回可查询的 task_id。"""
        task_name = name or f"event:{event_type}"
        return spawn_task(self.emit(event_type, **kwargs), name=task_name)

    async def _safe_call(self, event_type: str, callback: Callable, **kwargs):
        try:
            callback_name = getattr(callback, "__name__", str(callback))
            logger.debug(f"[EventBus] handle {event_type} -> {callback_name}")
            result = callback(**kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.error(f"Error in event callback {callback.__name__}: {e}")


# 全局单例
event_bus = EventBus()

# 预定义事件常量
EVENT_ORGANIZE_COMPLETE = "organize_complete"
EVENT_STRM_GENERATED = "strm_generated"
EVENT_TRANSFER_COMPLETE = "transfer_complete"
EVENT_MONITOR_NEW_LINK = "monitor_new_link"
EVENT_TRANSFER_RECEIVED = "transfer_received"
EVENT_TRANSFER_MOVED = "transfer_moved"
EVENT_TRANSFER_BATCH_REQUESTED = "transfer_batch_requested"
EVENT_TRANSFER_BATCH_PREPARED = "transfer_batch_prepared"
EVENT_TRANSFER_BATCH_ITEM_DONE = "transfer_batch_item_done"
EVENT_TRANSFER_BATCH_ITEM_FAILED = "transfer_batch_item_failed"
EVENT_TRANSFER_BATCH_DONE = "transfer_batch_done"
EVENT_TRANSFER_BATCH_DB_SYNC_REQUESTED = "transfer_batch_db_sync_requested"
EVENT_TRANSFER_BATCH_DB_SYNC_COMPLETED = "transfer_batch_db_sync_completed"
EVENT_STRM_BATCH_REQUESTED = "strm_batch_requested"
EVENT_STRM_BATCH_REWRITE_REQUESTED = "strm_batch_rewrite_requested"
EVENT_STRM_BATCH_COMPLETED = "strm_batch_completed"
EVENT_CLOUD115_DB_SYNC_COMPLETED = "cloud115_db_sync_completed"
EVENT_CLOUD115_FULL_SYNC_REQUESTED = "cloud115_full_sync_requested"
EVENT_CLOUD115_FULL_SYNC_DB_SYNC_FINISHED = "cloud115_db_sync_finished"
EVENT_CLOUD115_FULL_SYNC_MANIFEST_REFRESH_FINISHED = "cloud115_manifest_refresh_finished"
EVENT_CLOUD115_FULL_SYNC_STRM_REFRESH_FINISHED = "cloud115_strm_refresh_finished"
EVENT_CLOUD115_FULL_SYNC_MEDIA_LINKS_REFRESH_FINISHED = "cloud115_media_links_refresh_finished"
EVENT_CLOUD115_FULL_SYNC_COMPLETED = "cloud115_full_sync_completed"
EVENT_CLOUD115_COOKIE_INVALID = "cloud115_cookie_invalid"
EVENT_CLOUD115_COOKIE_RESTORED = "cloud115_cookie_restored"
EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED = "telegram_history_sync_requested"
EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_REQUESTED = "telegram_history_sync_chunk_requested"
EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_COMPLETED = "telegram_history_sync_chunk_completed"
EVENT_TELEGRAM_HISTORY_SYNC_CHANNEL_COMPLETED = "telegram_history_sync_channel_completed"
EVENT_TELEGRAM_HISTORY_SYNC_COMPLETED = "telegram_history_sync_completed"
EVENT_TELEGRAM_HISTORY_SYNC_FAILED = "telegram_history_sync_failed"
EVENT_ORGANIZE_START = "organize_start"
EVENT_ORGANIZE_FILE_DONE = "organize_file_done"
EVENT_ROLLBACK_START = "rollback_start"
EVENT_ROLLBACK_FILE_DONE = "rollback_file_done"
EVENT_ROLLBACK_COMPLETE = "rollback_complete"
# 任务追踪事件
EVENT_TASK_CREATED = "task_created"
EVENT_TASK_DONE = "task_done"
EVENT_TASK_ERROR = "task_error"