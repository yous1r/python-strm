import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.events import spawn_task, task_tracker


DEFAULT_POOL = "default"
LOCAL_DB_POOL = "local_db"
CLOUD_API_POOL = "cloud_api"


TaskFactory = Callable[[], Awaitable[Any] | Any]


@dataclass
class BackgroundTaskHandle:
    task_id: str
    task: asyncio.Task | None
    queued: bool
    pool: str
    duplicate_of: str | None = None


class BackgroundTaskCoordinator:
    def __init__(self, pool_limits: dict[str, int] | None = None):
        limits = {
            DEFAULT_POOL: 32,
            LOCAL_DB_POOL: 2,
            CLOUD_API_POOL: 5,
        }
        limits.update(pool_limits or {})
        self._pools = {
            pool: asyncio.Semaphore(max(int(limit), 1))
            for pool, limit in limits.items()
        }
        self._unique_tasks: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def get_pool(self, pool: str = DEFAULT_POOL) -> asyncio.Semaphore:
        pool_name = pool or DEFAULT_POOL
        if pool_name not in self._pools:
            self._pools[pool_name] = asyncio.Semaphore(1)
        return self._pools[pool_name]

    async def spawn(
        self,
        factory: TaskFactory,
        *,
        name: str,
        pool: str = DEFAULT_POOL,
    ) -> BackgroundTaskHandle:
        pool_name = pool or DEFAULT_POOL
        task_id, task = await task_tracker.create_task(
            self._run_in_pool(factory, pool_name),
            name=name,
            task_type="async",
            pool=pool_name,
        )
        return BackgroundTaskHandle(task_id=task_id, task=task, queued=True, pool=pool_name)

    async def spawn_unique(
        self,
        key: str,
        factory: TaskFactory,
        *,
        name: str,
        pool: str = DEFAULT_POOL,
    ) -> BackgroundTaskHandle:
        async with self._lock:
            existing_task_id = self._unique_tasks.get(key)
            if existing_task_id:
                return BackgroundTaskHandle(
                    task_id=existing_task_id,
                    task=None,
                    queued=False,
                    pool=pool or DEFAULT_POOL,
                    duplicate_of=existing_task_id,
                )

            async def unique_factory():
                try:
                    return await self._call_factory(factory)
                finally:
                    async with self._lock:
                        self._unique_tasks.pop(key, None)

            handle = await self.spawn(unique_factory, name=name, pool=pool)
            self._unique_tasks[key] = handle.task_id
            if handle.task:
                handle.task.add_done_callback(
                    lambda _task: spawn_task(
                        self._release_unique(key, handle.task_id),
                        name="background_unique_release",
                        pool=DEFAULT_POOL,
                    )
                )
            return handle

    async def run_in_pool(self, factory: TaskFactory, *, pool: str = DEFAULT_POOL) -> Any:
        return await self._run_in_pool(factory, pool or DEFAULT_POOL)

    async def _run_in_pool(self, factory: TaskFactory, pool: str) -> Any:
        async with self.get_pool(pool):
            return await self._call_factory(factory)

    async def _call_factory(self, factory: TaskFactory) -> Any:
        result = factory()
        if inspect.isawaitable(result):
            return await result
        return result

    async def _release_unique(self, key: str, task_id: str) -> None:
        async with self._lock:
            if self._unique_tasks.get(key) == task_id:
                self._unique_tasks.pop(key, None)


background_task_coordinator = BackgroundTaskCoordinator()


def get_pool_semaphore(pool: str = DEFAULT_POOL) -> asyncio.Semaphore:
    return background_task_coordinator.get_pool(pool)


async def run_in_background_pool(factory: TaskFactory, *, pool: str = DEFAULT_POOL) -> Any:
    return await background_task_coordinator.run_in_pool(factory, pool=pool)


def spawn_background_task(factory: TaskFactory, *, name: str, pool: str = DEFAULT_POOL) -> str:
    pool_name = pool or DEFAULT_POOL
    return spawn_task(
        background_task_coordinator.run_in_pool(factory, pool=pool_name),
        name=name,
        pool=pool_name,
    )
