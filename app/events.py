"""事件总线 + 后台任务追踪器"""
import asyncio
import time
import uuid
from typing import Callable, Dict, List, Any, Optional
from loguru import logger


class TaskTracker:
    """追踪所有 asyncio.create_task() 创建的后台任务，提供可观测性"""

    def __init__(self):
        self._tasks: Dict[str, dict] = {}  # task_id -> {name, type, created_at, status, error}
        self._lock = asyncio.Lock()

    async def spawn(
        self, coro, name: str = "", task_type: str = "async"
    ) -> str:
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


def spawn_task(coro, name: str = "", task_type: str = "async") -> str:
    """
    创建并追踪后台任务。替代裸 asyncio.create_task()。
    返回 task_id 供后续查询。
    """
    async def _deferred():
        return await task_tracker.spawn(coro, name=name, task_type=task_type)

    task_id = str(uuid.uuid4())[:8]
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
        callbacks = self._subscribers[event_type]
        logger.debug(f"[EventBus] emit {event_type} → {len(callbacks)} handler(s)")
        # 使用 spawn_task 追踪每个回调
        names = [f"event:{event_type}:{getattr(cb, '__name__', str(cb))}" for cb in callbacks]
        tasks = [
            asyncio.create_task(task_tracker.spawn(cb(**kwargs), name=names[i], task_type="async"))
            for i, cb in enumerate(callbacks)
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_call(self, callback: Callable, **kwargs):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(**kwargs)
            else:
                callback(**kwargs)
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
EVENT_ORGANIZE_START = "organize_start"
EVENT_ORGANIZE_FILE_DONE = "organize_file_done"
EVENT_ROLLBACK_START = "rollback_start"
EVENT_ROLLBACK_FILE_DONE = "rollback_file_done"
EVENT_ROLLBACK_COMPLETE = "rollback_complete"
# 任务追踪事件
EVENT_TASK_CREATED = "task_created"
EVENT_TASK_DONE = "task_done"
EVENT_TASK_ERROR = "task_error"