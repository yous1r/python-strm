import asyncio

import pytest


@pytest.mark.asyncio
async def test_background_task_coordinator_uses_isolated_pools():
    from app.events import task_tracker
    from app.utils.background_tasks import (
        CLOUD_API_POOL,
        LOCAL_DB_POOL,
        BackgroundTaskCoordinator,
    )

    task_tracker._tasks.clear()
    coordinator = BackgroundTaskCoordinator(
        pool_limits={
            LOCAL_DB_POOL: 1,
            CLOUD_API_POOL: 1,
        }
    )
    order: list[str] = []
    release_local = asyncio.Event()

    async def local_one():
        order.append("local_one_started")
        await release_local.wait()
        order.append("local_one_done")

    async def local_two():
        order.append("local_two_started")

    async def cloud_one():
        order.append("cloud_one_started")

    first = await coordinator.spawn(local_one, name="local_one", pool=LOCAL_DB_POOL)
    second = await coordinator.spawn(local_two, name="local_two", pool=LOCAL_DB_POOL)
    cloud = await coordinator.spawn(cloud_one, name="cloud_one", pool=CLOUD_API_POOL)

    await asyncio.sleep(0.05)

    assert order == ["local_one_started", "cloud_one_started"]
    assert first.queued is True
    assert second.queued is True
    assert cloud.queued is True

    active = await task_tracker.get_active()
    assert {item["name"] for item in active} >= {"local_one", "local_two", "cloud_one"}
    assert {item.get("pool") for item in active} >= {LOCAL_DB_POOL, CLOUD_API_POOL}

    release_local.set()
    await asyncio.gather(first.task, second.task, cloud.task)
    assert order == ["local_one_started", "cloud_one_started", "local_one_done", "local_two_started"]


@pytest.mark.asyncio
async def test_background_task_coordinator_deduplicates_active_unique_tasks():
    from app.utils.background_tasks import CLOUD_API_POOL, BackgroundTaskCoordinator

    coordinator = BackgroundTaskCoordinator(pool_limits={CLOUD_API_POOL: 1})
    release = asyncio.Event()
    started = asyncio.Event()
    factory_calls = 0

    async def heavy_job():
        nonlocal factory_calls
        factory_calls += 1
        started.set()
        await release.wait()

    first = await coordinator.spawn_unique(
        "cloud115_full_sync",
        heavy_job,
        name="cloud115_full_sync",
        pool=CLOUD_API_POOL,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    second = await coordinator.spawn_unique(
        "cloud115_full_sync",
        heavy_job,
        name="cloud115_full_sync",
        pool=CLOUD_API_POOL,
    )

    assert first.queued is True
    assert second.queued is False
    assert second.duplicate_of == first.task_id
    assert factory_calls == 1

    release.set()
    await first.task

    third = await coordinator.spawn_unique(
        "cloud115_full_sync",
        heavy_job,
        name="cloud115_full_sync",
        pool=CLOUD_API_POOL,
    )
    release.set()
    await third.task

    assert third.queued is True
    assert factory_calls == 2
