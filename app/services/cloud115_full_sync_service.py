import asyncio
import copy
import os
import uuid
from datetime import datetime

from loguru import logger

from app.config import get_config
from app.core.cloud115.db_sync import sync_directory
from app.core.cloud115.strm import generator_115
from app.events import (
    EVENT_CLOUD115_FULL_SYNC_COMPLETED,
    EVENT_CLOUD115_FULL_SYNC_DB_SYNC_FINISHED,
    EVENT_CLOUD115_FULL_SYNC_MANIFEST_REFRESH_FINISHED,
    EVENT_CLOUD115_FULL_SYNC_REQUESTED,
    EVENT_CLOUD115_FULL_SYNC_STRM_REFRESH_FINISHED,
    event_bus,
)


class Cloud115FullSyncService:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def start_full_sync(self, source: str = "debug") -> dict[str, object]:
        task_id = str(uuid.uuid4())[:8]
        task = {
            "task_id": task_id,
            "source": source,
            "status": "running",
            "current_stage": "queued",
            "started_at": self._now(),
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

        async with self._lock:
            self._tasks[task_id] = task
            self._trim_tasks_locked(limit=100)

        event_bus.emit_background(
            EVENT_CLOUD115_FULL_SYNC_REQUESTED,
            name=f"cloud115_full_sync:{source}",
            task_id=task_id,
            source=source,
        )
        return {
            "task_id": task_id,
            "status": task["status"],
            "current_stage": task["current_stage"],
            "message": "115 全链路验证任务已启动",
        }

    async def run_scheduled_full_sync(self) -> dict[str, object]:
        return await self.start_full_sync(source="scheduler")

    async def get_task(self, task_id: str) -> dict[str, object] | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return copy.deepcopy(task) if task else None

    def build_sync_dirs(self) -> list[dict[str, object]]:
        config = get_config()
        dirs: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()

        for sync_dir in config.cloud115.sync_dirs:
            key = (str(sync_dir.dir_id), "sync_dir")
            if key in seen:
                continue
            seen.add(key)
            dirs.append(
                {
                    "dir_id": str(sync_dir.dir_id),
                    "dir_name": sync_dir.name,
                    "recursive": True,
                    "output_dir": os.path.join(config.strm.output_dir, sync_dir.name),
                    "role": "sync_dir",
                    "strm_enabled": True,
                }
            )

        transfer_cfg = config.transfer
        if transfer_cfg.enabled:
            if transfer_cfg.temp_dir_id and transfer_cfg.temp_dir_id != "0":
                dirs.append(
                    {
                        "dir_id": str(transfer_cfg.temp_dir_id),
                        "dir_name": "temp_dir",
                        "recursive": True,
                        "output_dir": "",
                        "role": "temp_dir",
                        "strm_enabled": False,
                    }
                )
            if transfer_cfg.archive_dir_id and transfer_cfg.archive_dir_id != "0":
                dirs.append(
                    {
                        "dir_id": str(transfer_cfg.archive_dir_id),
                        "dir_name": "archive_dir",
                        "recursive": True,
                        "output_dir": "",
                        "role": "archive_dir",
                        "strm_enabled": False,
                    }
                )

        return dirs

    async def run_db_sync_step(self, dirs: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
        sync_dirs = dirs or self.build_sync_dirs()
        results: list[dict[str, object]] = []
        for item in sync_dirs:
            count = await sync_directory(
                str(item["dir_id"]),
                str(item.get("dir_name") or item["dir_id"]),
                bool(item.get("recursive", True)),
            )
            result = dict(item)
            result["count"] = count
            results.append(result)
        return results

    async def run_manifest_refresh_step(self, dirs: list[dict[str, object]]) -> dict[str, object]:
        config = get_config()
        results: list[dict[str, object]] = []
        refresh_dirs: list[dict[str, object]] = []

        for item in dirs:
            if not item.get("strm_enabled") or not item.get("output_dir"):
                continue

            cleanup = await generator_115.cleanup_invalid_strm_records(
                dir_id=str(item["dir_id"]),
                output_dir=str(item["output_dir"]),
                root_output_dir=config.strm.output_dir,
            )
            results.append(
                {
                    "dir_id": str(item["dir_id"]),
                    "dir_name": str(item.get("dir_name") or item["dir_id"]),
                    "output_dir": str(item["output_dir"]),
                    "cleaned_records": cleanup.get("records", 0),
                    "deleted_strm_files": cleanup.get("files", 0),
                }
            )
            refresh_dirs.append(dict(item))

        return {"results": results, "dirs": refresh_dirs}

    async def run_strm_refresh_step(self, dirs: list[dict[str, object]]) -> list[dict[str, object]]:
        config = get_config()
        results: list[dict[str, object]] = []

        for item in dirs:
            generated = await generator_115.batch_generate(
                dir_id=str(item["dir_id"]),
                output_dir=str(item["output_dir"]),
                base_url=config.strm.base_url,
                recursive=bool(item.get("recursive", True)),
                root_output_dir=config.strm.output_dir,
                force=False,
                cleanup_invalid=False,
            )
            results.append(
                {
                    "dir_id": str(item["dir_id"]),
                    "dir_name": str(item.get("dir_name") or item["dir_id"]),
                    "output_dir": str(item["output_dir"]),
                    "generated_count": len(generated),
                    "generated_files": generated[:10],
                }
            )

        return results

    async def handle_full_sync_requested(self, task_id: str, source: str = "debug", **kwargs):
        await self._update_task(task_id, current_stage="db_sync")
        try:
            db_sync_results = await self.run_db_sync_step()
            await self._update_task(
                task_id,
                dirs=db_sync_results,
                db_sync_results=db_sync_results,
            )
            await self._update_stats(
                task_id,
                db_sync_rows=sum(int(item.get("count", 0)) for item in db_sync_results),
            )
            await event_bus.emit(
                EVENT_CLOUD115_FULL_SYNC_DB_SYNC_FINISHED,
                task_id=task_id,
                source=source,
                dirs=db_sync_results,
            )
        except Exception as exc:
            await self._mark_failed(task_id, "db_sync", exc)

    async def handle_db_sync_finished(self, task_id: str, dirs: list[dict[str, object]], source: str = "debug", **kwargs):
        await self._update_task(task_id, current_stage="manifest_refresh")
        try:
            manifest_data = await self.run_manifest_refresh_step(dirs)
            manifest_results = manifest_data["results"]
            refresh_dirs = manifest_data["dirs"]
            await self._update_task(task_id, manifest_results=manifest_results)
            await self._update_stats(
                task_id,
                cleaned_records=sum(int(item.get("cleaned_records", 0)) for item in manifest_results),
                deleted_strm_files=sum(int(item.get("deleted_strm_files", 0)) for item in manifest_results),
            )
            await event_bus.emit(
                EVENT_CLOUD115_FULL_SYNC_MANIFEST_REFRESH_FINISHED,
                task_id=task_id,
                source=source,
                dirs=refresh_dirs,
            )
        except Exception as exc:
            await self._mark_failed(task_id, "manifest_refresh", exc)

    async def handle_manifest_refresh_finished(
        self,
        task_id: str,
        dirs: list[dict[str, object]],
        source: str = "debug",
        **kwargs,
    ):
        await self._update_task(task_id, current_stage="strm_refresh")
        try:
            strm_results = await self.run_strm_refresh_step(dirs)
            await self._update_task(task_id, strm_results=strm_results)
            await self._update_stats(
                task_id,
                generated_strm_files=sum(int(item.get("generated_count", 0)) for item in strm_results),
            )
            await event_bus.emit(
                EVENT_CLOUD115_FULL_SYNC_STRM_REFRESH_FINISHED,
                task_id=task_id,
                source=source,
            )
        except Exception as exc:
            await self._mark_failed(task_id, "strm_refresh", exc)

    async def handle_strm_refresh_finished(self, task_id: str, source: str = "debug", **kwargs):
        await self._update_task(
            task_id,
            status="completed",
            current_stage="completed",
            finished_at=self._now(),
        )
        await event_bus.emit(
            EVENT_CLOUD115_FULL_SYNC_COMPLETED,
            task_id=task_id,
            source=source,
        )

    async def _update_task(self, task_id: str, **fields):
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.update(fields)

    async def _update_stats(self, task_id: str, **stats):
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task_stats = task.setdefault("stats", {})
            for key, value in stats.items():
                task_stats[key] = int(value)

    async def _mark_failed(self, task_id: str, stage: str, exc: Exception):
        logger.error(f"[Cloud115FullSync] stage={stage} task_id={task_id} failed: {exc}")
        await self._update_task(
            task_id,
            status="failed",
            current_stage=stage,
            finished_at=self._now(),
            error=str(exc),
        )

    def _trim_tasks_locked(self, limit: int):
        if len(self._tasks) <= limit:
            return
        ordered = sorted(
            self._tasks.items(),
            key=lambda item: item[1].get("started_at") or "",
        )
        for task_id, _ in ordered[:-limit]:
            self._tasks.pop(task_id, None)

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")


cloud115_full_sync_service = Cloud115FullSyncService()


def init_cloud115_full_sync_events():
    event_bus.subscribe(
        EVENT_CLOUD115_FULL_SYNC_REQUESTED,
        cloud115_full_sync_service.handle_full_sync_requested,
    )
    event_bus.subscribe(
        EVENT_CLOUD115_FULL_SYNC_DB_SYNC_FINISHED,
        cloud115_full_sync_service.handle_db_sync_finished,
    )
    event_bus.subscribe(
        EVENT_CLOUD115_FULL_SYNC_MANIFEST_REFRESH_FINISHED,
        cloud115_full_sync_service.handle_manifest_refresh_finished,
    )
    event_bus.subscribe(
        EVENT_CLOUD115_FULL_SYNC_STRM_REFRESH_FINISHED,
        cloud115_full_sync_service.handle_strm_refresh_finished,
    )
    logger.info("[Cloud115FullSync] 已注册 115 全链路验证事件处理器")