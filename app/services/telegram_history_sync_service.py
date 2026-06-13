from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

from loguru import logger
from pydantic import BaseModel, Field

from app.config import get_config
from app.core.monitor.telegram_runtime import extract_message_text, parse_channel_reference
from app.database import (
    get_telegram_history_sync_checkpoint,
    upsert_telegram_history_sync_checkpoint,
    upsert_telegram_monitor_state,
)
from app.events import (
    EVENT_TELEGRAM_HISTORY_SYNC_CHANNEL_COMPLETED,
    EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_COMPLETED,
    EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_REQUESTED,
    EVENT_TELEGRAM_HISTORY_SYNC_COMPLETED,
    EVENT_TELEGRAM_HISTORY_SYNC_FAILED,
    EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED,
    event_bus,
)
from app.services.telegram_service import (
    TelegramValidationError,
    _acquire_client,
    _dispatch_scraped_message,
    validate_monitor_request,
)
from app.utils.background_tasks import CLOUD_API_POOL


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date_to_datetime(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.combine(parsed, time.max if end_of_day else time.min, tzinfo=timezone.utc)


def _subtract_months(base: datetime, months: int) -> datetime:
    year = base.year
    month = base.month - months
    while month <= 0:
        year -= 1
        month += 12
    day = min(base.day, 28)
    return base.replace(year=year, month=month, day=day)


def _normalize_message_datetime(message_date) -> datetime | None:
    return _coerce_datetime(message_date)


@dataclass
class TelegramHistorySyncChunk:
    channel_ref: str
    range_start: str
    range_end: str

    @property
    def checkpoint_key(self) -> str:
        return f"{self.channel_ref}|{self.range_start}|{self.range_end}"


class TelegramHistorySyncRequest(BaseModel):
    api_id: str
    api_hash: str
    bot_token: str = ""
    proxy: str = ""
    channels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    mode: str = "relative_range"
    relative_value: int = 6
    relative_unit: str = "months"
    date_start: str = ""
    date_end: str = ""
    chunk_days: int = 7
    emit_events: bool = False
    source: str = "manual"


class TelegramHistorySyncService:
    def __init__(self):
        self._events_initialized = False
        self._active_request_keys: dict[str, str] = {}

    def init_event_subscriptions(self) -> None:
        if self._events_initialized:
            return
        event_bus.subscribe(EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED, self.handle_history_sync_requested)
        self._events_initialized = True

    def build_request_from_config(
        self,
        *,
        source: str,
        channels: Iterable[str] | None = None,
        keywords: Iterable[str] | None = None,
        mode: str | None = None,
        relative_value: int | None = None,
        relative_unit: str | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        chunk_days: int | None = None,
        emit_events: bool | None = None,
    ) -> TelegramHistorySyncRequest:
        cfg = get_config().monitor.telegram
        history_cfg = getattr(cfg, "history_sync", None)
        history_mode = mode or getattr(history_cfg, "mode", "relative_range")
        return TelegramHistorySyncRequest(
            api_id=cfg.api_id,
            api_hash=cfg.api_hash,
            bot_token=getattr(cfg, "bot_token", ""),
            proxy=cfg.proxy,
            channels=list(channels if channels is not None else (cfg.channels or [])),
            keywords=list(keywords if keywords is not None else (cfg.keywords or [])),
            mode=history_mode,
            relative_value=relative_value if relative_value is not None else int(getattr(history_cfg, "relative_value", 6) or 6),
            relative_unit=relative_unit or getattr(history_cfg, "relative_unit", "months"),
            date_start=date_start if date_start is not None else getattr(history_cfg, "date_start", ""),
            date_end=date_end if date_end is not None else getattr(history_cfg, "date_end", ""),
            chunk_days=chunk_days if chunk_days is not None else int(getattr(history_cfg, "chunk_days", 7) or 7),
            emit_events=emit_events if emit_events is not None else bool(getattr(history_cfg, "emit_new_link_events", False)),
            source=source,
        )

    def build_request_for_startup(self) -> TelegramHistorySyncRequest:
        cfg = get_config().monitor.telegram
        startup_mode = getattr(cfg, "startup_sync", "disabled")
        if startup_mode == "disabled":
            raise TelegramValidationError("启动历史同步已禁用")
        if startup_mode == "latest":
            return self.build_request_from_config(
                source="startup",
                mode="relative_range",
                relative_value=7,
                relative_unit="days",
            )
        return self.build_request_from_config(source="startup")

    def build_request_for_schedule(self) -> TelegramHistorySyncRequest:
        cfg = get_config().monitor.telegram
        history_cfg = getattr(cfg, "history_sync", None)
        return self.build_request_from_config(
            source="scheduled",
            emit_events=bool(getattr(history_cfg, "emit_new_link_events", False)),
        )

    def queue_history_sync_request(self, request: TelegramHistorySyncRequest, *, name: str | None = None) -> dict[str, object]:
        request_key = self._build_request_key(request)
        existing_task_id = self._active_request_keys.get(request_key)
        if existing_task_id:
            return {
                "status": "duplicate",
                "queued": False,
                "task_id": existing_task_id,
                "event": EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED,
                "source": request.source,
                "message": "同一 Telegram 历史同步任务正在运行，已跳过重复投递",
            }

        payload = request.model_dump()
        task_id = event_bus.emit_background(
            EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED,
            name=name or f"telegram_history_sync:{request.source}",
            pool=CLOUD_API_POOL,
            request=payload,
        )
        self._active_request_keys[request_key] = task_id
        return {
            "status": "success",
            "queued": True,
            "task_id": task_id,
            "event": EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED,
            "source": request.source,
            "message": "Telegram 历史同步任务已加入后台队列",
        }

    def _build_request_key(self, request: TelegramHistorySyncRequest) -> str:
        payload = request.model_dump(exclude={"api_hash", "bot_token", "source"})
        payload["channels"] = sorted(payload.get("channels") or [])
        payload["keywords"] = sorted(payload.get("keywords") or [])
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _release_request_key(self, request: TelegramHistorySyncRequest) -> None:
        request_key = self._build_request_key(request)
        self._active_request_keys.pop(request_key, None)

    def resolve_requested_range(
        self,
        request: TelegramHistorySyncRequest,
        *,
        now: datetime | None = None,
        channel_start: datetime | None = None,
        channel_end: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        current = now or _utc_now()
        if request.mode == "all":
            if channel_start is None or channel_end is None:
                raise TelegramValidationError("全量历史同步缺少频道消息边界")
            return channel_start, channel_end
        if request.mode == "relative_range":
            value = max(int(request.relative_value or 0), 1)
            unit = (request.relative_unit or "months").lower()
            if unit == "days":
                start = current - timedelta(days=value)
            elif unit == "weeks":
                start = current - timedelta(weeks=value)
            elif unit == "months":
                start = _subtract_months(current, value)
            else:
                raise TelegramValidationError("relative_unit 仅支持 days/weeks/months")
            return start, current
        if request.mode == "date_range":
            if not request.date_start or not request.date_end:
                raise TelegramValidationError("日期区间同步必须提供 date_start 和 date_end")
            start = _date_to_datetime(request.date_start, end_of_day=False)
            end = _date_to_datetime(request.date_end, end_of_day=True)
            if start > end:
                raise TelegramValidationError("date_start 不能晚于 date_end")
            return start, end
        raise TelegramValidationError("历史同步模式仅支持 all / relative_range / date_range")

    def build_chunks(self, channel_ref: str, start: datetime, end: datetime, *, chunk_days: int) -> list[TelegramHistorySyncChunk]:
        normalized_chunk_days = max(int(chunk_days or 0), 1)
        chunks: list[TelegramHistorySyncChunk] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=normalized_chunk_days) - timedelta(microseconds=1), end)
            chunks.append(
                TelegramHistorySyncChunk(
                    channel_ref=channel_ref,
                    range_start=cursor.isoformat(),
                    range_end=chunk_end.isoformat(),
                )
            )
            cursor = chunk_end + timedelta(microseconds=1)
        return chunks

    async def inspect_channel_bounds(self, client_to_use, channel_ref: str) -> tuple[datetime | None, datetime | None]:
        parsed_channel = parse_channel_reference(channel_ref)
        latest_messages = await client_to_use.get_messages(parsed_channel, limit=1)
        latest_message = latest_messages[0] if latest_messages else None
        if latest_message is None:
            return None, None

        earliest_message = None
        async for message in client_to_use.iter_messages(parsed_channel, limit=1, reverse=True):
            earliest_message = message
            break

        return (
            _normalize_message_datetime(getattr(earliest_message, "date", None)),
            _normalize_message_datetime(getattr(latest_message, "date", None)),
        )

    async def handle_history_sync_requested(self, request: dict | TelegramHistorySyncRequest):
        sync_request = request if isinstance(request, TelegramHistorySyncRequest) else TelegramHistorySyncRequest(**request)
        client_to_use = None
        disconnect_after = False

        try:
            normalized_channels = validate_monitor_request(
                sync_request.api_id,
                sync_request.api_hash,
                sync_request.channels,
                empty_channels_message="未配置任何监听频道，无法执行历史同步",
            )
            client_to_use, disconnect_after, is_auth, auth_error = await _acquire_client(
                sync_request.api_id,
                sync_request.api_hash,
                bot_token=sync_request.bot_token,
                proxy=sync_request.proxy,
            )

            if auth_error or not is_auth:
                raise TelegramValidationError(auth_error or "Telegram client not authorized")

            channel_results = []
            total_processed = 0
            total_inserted = 0

            for channel_ref in normalized_channels:
                channel_start = channel_end = None
                if sync_request.mode == "all":
                    channel_start, channel_end = await self.inspect_channel_bounds(client_to_use, channel_ref)
                    if channel_start is None or channel_end is None:
                        channel_results.append({
                            "channel_ref": channel_ref,
                            "status": "skipped",
                            "reason": "channel_has_no_messages",
                            "processed": 0,
                            "inserted": 0,
                            "chunks": [],
                        })
                        continue

                range_start, range_end = self.resolve_requested_range(
                    sync_request,
                    channel_start=channel_start,
                    channel_end=channel_end,
                )
                chunks = self.build_chunks(channel_ref, range_start, range_end, chunk_days=sync_request.chunk_days)
                chunk_results = []
                channel_processed = 0
                channel_inserted = 0
                highest_message_id = None
                highest_message_date = None

                for chunk in chunks:
                    event_bus.emit_background(
                        EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_REQUESTED,
                        name=f"telegram_history_sync_chunk:{channel_ref}",
                        channel_ref=channel_ref,
                        range_start=chunk.range_start,
                        range_end=chunk.range_end,
                        source=sync_request.source,
                    )
                    chunk_result = await self._run_chunk(client_to_use, sync_request, chunk)
                    chunk_results.append(chunk_result)
                    channel_processed += chunk_result["processed"]
                    channel_inserted += chunk_result["inserted"]
                    if chunk_result.get("last_message_id") is not None:
                        if highest_message_id is None or chunk_result["last_message_id"] > highest_message_id:
                            highest_message_id = chunk_result["last_message_id"]
                            highest_message_date = chunk_result.get("last_message_date")

                await upsert_telegram_monitor_state(
                    channel_ref=channel_ref,
                    resolved_channel_id=str(parse_channel_reference(channel_ref)),
                    last_message_id=highest_message_id,
                    last_message_date=highest_message_date,
                    last_error="",
                )

                channel_result = {
                    "channel_ref": channel_ref,
                    "status": "success",
                    "processed": channel_processed,
                    "inserted": channel_inserted,
                    "chunks": chunk_results,
                    "last_message_id": highest_message_id,
                }
                channel_results.append(channel_result)
                total_processed += channel_processed
                total_inserted += channel_inserted
                event_bus.emit_background(
                    EVENT_TELEGRAM_HISTORY_SYNC_CHANNEL_COMPLETED,
                    name=f"telegram_history_sync_channel_completed:{channel_ref}",
                    channel_ref=channel_ref,
                    result=channel_result,
                    source=sync_request.source,
                )

            summary = {
                "status": "success",
                "source": sync_request.source,
                "mode": sync_request.mode,
                "processed": total_processed,
                "inserted": total_inserted,
                "channels": channel_results,
            }
            event_bus.emit_background(
                EVENT_TELEGRAM_HISTORY_SYNC_COMPLETED,
                name=f"telegram_history_sync_completed:{sync_request.source}",
                result=summary,
            )
            return summary
        except Exception as exc:
            logger.error(f"Telegram history sync failed: {exc}")
            event_bus.emit_background(
                EVENT_TELEGRAM_HISTORY_SYNC_FAILED,
                name=f"telegram_history_sync_failed:{sync_request.source}",
                source=sync_request.source,
                error=str(exc),
                request=sync_request.model_dump(),
            )
            raise
        finally:
            self._release_request_key(sync_request)
            if disconnect_after and client_to_use:
                await client_to_use.disconnect()

    async def _run_chunk(
        self,
        client_to_use,
        request: TelegramHistorySyncRequest,
        chunk: TelegramHistorySyncChunk,
    ) -> dict[str, object]:
        existing_checkpoint = await get_telegram_history_sync_checkpoint(chunk.checkpoint_key)
        if existing_checkpoint and existing_checkpoint.get("status") == "completed":
            return {
                "channel_ref": chunk.channel_ref,
                "range_start": chunk.range_start,
                "range_end": chunk.range_end,
                "status": "skipped",
                "processed": existing_checkpoint.get("processed_count", 0),
                "inserted": existing_checkpoint.get("inserted_count", 0),
                "last_message_id": existing_checkpoint.get("last_message_id"),
                "last_message_date": existing_checkpoint.get("last_message_date"),
            }

        parsed_channel = parse_channel_reference(chunk.channel_ref)
        range_start = _coerce_datetime(chunk.range_start)
        range_end = _coerce_datetime(chunk.range_end)
        resume_last_message_id = existing_checkpoint.get("last_message_id") if existing_checkpoint else None
        valid_kws = [keyword.strip().lower() for keyword in (request.keywords or []) if keyword and keyword.strip()]
        processed = 0
        inserted = 0
        highest_message_id = resume_last_message_id
        highest_message_date = existing_checkpoint.get("last_message_date") if existing_checkpoint else None

        try:
            async for message in client_to_use.iter_messages(parsed_channel, limit=None):
                message_dt = _normalize_message_datetime(getattr(message, "date", None))
                if message_dt is None:
                    continue
                if resume_last_message_id and getattr(message, "id", 0) >= resume_last_message_id:
                    continue
                if range_end and message_dt > range_end:
                    continue
                if range_start and message_dt < range_start:
                    break

                text = extract_message_text(message)
                if valid_kws and not any(keyword in text.lower() for keyword in valid_kws):
                    continue

                processed += 1
                resources = await _dispatch_scraped_message(parsed_channel, message, emit_events=request.emit_events)
                inserted += len(resources)
                if highest_message_id is None or message.id > highest_message_id:
                    highest_message_id = message.id
                    highest_message_date = str(message.date)

            await upsert_telegram_history_sync_checkpoint(
                chunk.checkpoint_key,
                channel_ref=chunk.channel_ref,
                range_start=chunk.range_start,
                range_end=chunk.range_end,
                status="completed",
                last_message_id=highest_message_id,
                last_message_date=highest_message_date,
                processed_count=processed,
                inserted_count=inserted,
                last_error="",
            )
            result = {
                "channel_ref": chunk.channel_ref,
                "range_start": chunk.range_start,
                "range_end": chunk.range_end,
                "status": "completed",
                "processed": processed,
                "inserted": inserted,
                "last_message_id": highest_message_id,
                "last_message_date": highest_message_date,
            }
            event_bus.emit_background(
                EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_COMPLETED,
                name=f"telegram_history_sync_chunk_completed:{chunk.channel_ref}",
                result=result,
                source=request.source,
            )
            return result
        except Exception as exc:
            await upsert_telegram_history_sync_checkpoint(
                chunk.checkpoint_key,
                channel_ref=chunk.channel_ref,
                range_start=chunk.range_start,
                range_end=chunk.range_end,
                status="failed",
                last_message_id=highest_message_id,
                last_message_date=highest_message_date,
                processed_count=processed,
                inserted_count=inserted,
                last_error=str(exc),
            )
            raise


telegram_history_sync_service = TelegramHistorySyncService()


def init_telegram_history_sync_events() -> None:
    telegram_history_sync_service.init_event_subscriptions()
