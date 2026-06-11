import asyncio
from typing import Iterable

from loguru import logger

from app.config import get_config
from app.core.monitor.telegram import telegram_monitor
from app.core.monitor.telegram_runtime import (
    build_telegram_client,
    extract_message_text,
    extract_message_torrent_files,
    parse_channel_reference,
    parse_channels,
)
from app.database import (
    get_db_conn,
    get_telegram_monitor_state,
    list_telegram_monitor_states,
    upsert_telegram_monitor_state,
)
from app.events import EVENT_MONITOR_NEW_LINK, event_bus


class TelegramValidationError(ValueError):
    """Telegram 请求参数校验失败。"""


def validate_monitor_request(
    api_id: str,
    api_hash: str,
    channels: Iterable[str] | None,
    *,
    empty_channels_message: str,
) -> list[str]:
    if not api_id or not api_hash:
        raise TelegramValidationError("API ID 和 API Hash 不能为空")

    normalized_channels = [channel.strip() for channel in (channels or []) if channel and channel.strip()]
    if not normalized_channels:
        raise TelegramValidationError(empty_channels_message)

    return normalized_channels


async def restart_monitor(delay_seconds: float = 1.0) -> None:
    """热重启 Telegram 监听器。"""
    await telegram_monitor.stop()
    if get_config().monitor.telegram.enabled:
        await asyncio.sleep(delay_seconds)
        await telegram_monitor.start()


async def get_monitor_status() -> dict[str, object]:
    cfg = get_config().monitor.telegram
    states = await list_telegram_monitor_states()
    running = bool(telegram_monitor.client and telegram_monitor.client.is_connected())
    return {
        "enabled": cfg.enabled,
        "mode": getattr(cfg, "mode", "auto"),
        "running": running,
        "channels": states,
    }


async def test_monitor_connection(
    api_id: str,
    api_hash: str,
    bot_token: str = "",
    proxy: str = "",
    channels: Iterable[str] | None = None,
) -> dict[str, str]:
    normalized_channels = validate_monitor_request(
        api_id,
        api_hash,
        channels,
        empty_channels_message="未配置频道！请在前端添加至少一个监听频道后再试。",
    )
    first_channel = normalized_channels[0]
    parsed_channel = parse_channel_reference(first_channel)

    client_to_use, disconnect_after, is_auth, auth_error = await _acquire_client(
        api_id,
        api_hash,
        bot_token=bot_token,
        proxy=proxy,
    )

    try:
        if auth_error:
            return {"status": "error", "message": auth_error}

        if not is_auth:
            return {
                "status": "error",
                "message": "连接成功，但尚未登录。请填写 Bot Token 或在后端运行 python login_tg.py 完成扫码登录。",
            }

        try:
            messages = await client_to_use.get_messages(parsed_channel, limit=1)
            msg_text = messages[0].text if messages and messages[0].text else "[图片/非文本消息或空消息]"
            success_msg = f"连接并鉴权成功！\n成功读取到频道 [{first_channel}] 的最新一条消息：\n\n{msg_text}"
        except Exception as exc:
            success_msg = (
                f"连接并鉴权成功！但读取频道 [{first_channel}] 失败，可能您还未加入该频道，或者权限不足。\n"
                f"错误信息: {exc}"
            )

        return {"status": "success", "message": success_msg}
    finally:
        if disconnect_after:
            await client_to_use.disconnect()


async def scrape_monitor_history(
    api_id: str,
    api_hash: str,
    bot_token: str = "",
    proxy: str = "",
    channels: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
) -> None:
    normalized_channels = validate_monitor_request(
        api_id,
        api_hash,
        channels,
        empty_channels_message="未配置任何监听频道，无法抓取",
    )
    client_to_use, disconnect_after, is_auth, auth_error = await _acquire_client(
        api_id,
        api_hash,
        bot_token=bot_token,
        proxy=proxy,
    )

    if auth_error:
        logger.error(f"Scrape History failed: {auth_error}")
        if disconnect_after:
            await client_to_use.disconnect()
        return

    if not is_auth:
        logger.error("Scrape History failed: Telegram client not authorized.")
        if disconnect_after:
            await client_to_use.disconnect()
        return

    try:
        total_links_found = 0
        valid_kws = [keyword.strip().lower() for keyword in (keywords or []) if keyword and keyword.strip()]

        for channel_ref in normalized_channels:
            parsed_channel = parse_channel_reference(channel_ref)
            try:
                msg_count = 0
                async for message in client_to_use.iter_messages(parsed_channel, limit=None):
                    text = extract_message_text(message)
                    if valid_kws and not any(kw in text.lower() for kw in valid_kws):
                        continue
                    msg_count += 1
                    await _throttle_scrape(msg_count)
                    resources = await _dispatch_scraped_message(parsed_channel, message)
                    total_links_found += len(resources)
            except Exception as exc:
                logger.error(f"Failed to scrape channel {channel_ref}: {exc}")

        logger.info(f"Telegram history scraping finished. Found {total_links_found} links added to queue.")
    except Exception as exc:
        logger.error(f"Error during Telegram history scraping: {exc}")
    finally:
        if disconnect_after:
            await client_to_use.disconnect()


async def sync_single_channel(
    api_id: str,
    api_hash: str,
    *,
    bot_token: str = "",
    proxy: str = "",
    channels: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    channel_ref: str,
    emit_events: bool = True,
    startup_mode: str = "incremental",
    limit: int | None = None,
) -> dict[str, object]:
    normalized_channels = validate_monitor_request(
        api_id,
        api_hash,
        channels,
        empty_channels_message="未配置任何监听频道，无法抓取",
    )
    if channel_ref not in normalized_channels:
        raise TelegramValidationError("目标频道未在配置列表中")

    client_to_use, disconnect_after, is_auth, auth_error = await _acquire_client(
        api_id,
        api_hash,
        bot_token=bot_token,
        proxy=proxy,
    )
    if auth_error or not is_auth:
        if disconnect_after:
            await client_to_use.disconnect()
        raise TelegramValidationError(auth_error or "Telegram client not authorized")

    try:
        return await _sync_channel_history(
            client_to_use,
            channel_ref,
            keywords=keywords,
            emit_events=emit_events,
            startup_mode=startup_mode,
            limit=limit or getattr(get_config().monitor.telegram, "history_limit", 100),
        )
    finally:
        if disconnect_after:
            await client_to_use.disconnect()


async def sync_configured_channels(
    api_id: str,
    api_hash: str,
    *,
    bot_token: str = "",
    proxy: str = "",
    channels: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    emit_events: bool = True,
    startup_mode: str = "incremental",
    limit: int | None = None,
) -> dict[str, object]:
    """抓取历史消息"""
    normalized_channels = validate_monitor_request(
        api_id,
        api_hash,
        channels,
        empty_channels_message="未配置任何监听频道，无法抓取",
    )
    results = []
    all_resources: list[dict] = []
    for channel_ref in normalized_channels:
        try:
            channel_result = await sync_single_channel(
                api_id,
                api_hash,
                bot_token=bot_token,
                proxy=proxy,
                channels=normalized_channels,
                keywords=keywords,
                channel_ref=channel_ref,
                emit_events=emit_events,
                startup_mode=startup_mode,
                limit=limit,
            )
            results.append(channel_result)
            all_resources.extend(channel_result.get("resources", []))
        except Exception as exc:
            logger.error(f"Failed to sync channel {channel_ref}: {exc}")
            results.append({"channel_ref": channel_ref, "processed": 0, "inserted": 0, "resources": [], "error": str(exc)})
    return {"status": "success", "channels": results, "resources": all_resources}


async def _acquire_client(
    api_id: str,
    api_hash: str,
    *,
    bot_token: str = "",
    proxy: str = "",
):
    if telegram_monitor.client and telegram_monitor.client.is_connected():
        is_auth = await telegram_monitor.client.is_user_authorized()
        return telegram_monitor.client, False, is_auth, None

    client_to_use = build_telegram_client(api_id, api_hash, proxy)
    await client_to_use.connect()

    if await client_to_use.is_user_authorized():
        return client_to_use, True, True, None

    if bot_token:
        try:
            await client_to_use.start(bot_token=bot_token)
            return client_to_use, True, True, None
        except Exception as exc:
            return client_to_use, True, False, f"Bot Token 登录失败: {exc}"

    return client_to_use, True, False, None


async def _dispatch_scraped_message(channel: int | str, message, *, emit_events: bool = True) -> list[dict]:
    text = extract_message_text(message)
    torrent_files = extract_message_torrent_files(message)
    message_channel_id = getattr(message, "chat_id", None)
    persisted_channel_id = str(message_channel_id) if message_channel_id is not None else str(channel)
    resources = await telegram_monitor.ingest_message(
        text,
        message_id=message.id,
        channel_id=persisted_channel_id,
        msg_date=str(message.date),
        torrent_files=torrent_files,
    )

    if emit_events:
        for link_data in resources:
            event_bus.emit_background(EVENT_MONITOR_NEW_LINK, link_data=link_data, source="telegram")

    return resources


async def _sync_channel_history(
    client_to_use,
    channel_ref: str,
    *,
    keywords: Iterable[str] | None = None,
    emit_events: bool = True,
    startup_mode: str = "incremental",
    limit: int = 100,
) -> dict[str, object]:
    parsed_channel = parse_channel_reference(channel_ref)
    state = await get_telegram_monitor_state(channel_ref)
    valid_kws = [keyword.strip().lower() for keyword in (keywords or []) if keyword and keyword.strip()]
    has_existing_resources = await _channel_has_resources(channel_ref, str(parsed_channel))

    if startup_mode == "disabled":
        return {"channel_ref": channel_ref, "processed": 0, "inserted": 0, "skipped": True}

    processed = 0
    inserted = 0
    resources: list[dict] = []
    stop_message_id = state["last_message_id"] if state else None
    highest_id = state["last_message_id"] if state else None
    highest_date = state["last_message_date"] if state else None

    if state and not has_existing_resources:
        stop_message_id = None
        highest_id = None
        highest_date = None

    async for message in client_to_use.iter_messages(parsed_channel, limit=limit):
        if stop_message_id and message.id <= stop_message_id:
            break
        if startup_mode == "latest" and state is None:
            highest_id = message.id
            highest_date = str(message.date)
            break

        text = extract_message_text(message)
        if valid_kws and not any(kw in text.lower() for kw in valid_kws):
            continue

        processed += 1
        message_resources = await _dispatch_scraped_message(parsed_channel, message, emit_events=emit_events)
        inserted += len(message_resources)
        resources.extend(message_resources)
        if highest_id is None or message.id > highest_id:
            highest_id = message.id
            highest_date = str(message.date)

    await upsert_telegram_monitor_state(
        channel_ref=channel_ref,
        resolved_channel_id=str(parsed_channel),
        last_message_id=highest_id,
        last_message_date=highest_date,
        last_error="",
    )
    return {
        "channel_ref": channel_ref,
        "processed": processed,
        "inserted": inserted,
        "resources": resources,
        "last_message_id": highest_id,
    }


async def _channel_has_resources(channel_ref: str, channel_id: str) -> bool:
    async with get_db_conn() as db:
        async with db.execute(
            '''
            SELECT 1
            FROM tg_resources
            WHERE channel_id IN (?, ?)
            LIMIT 1
            ''',
            (channel_ref, channel_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def _throttle_scrape(message_count: int) -> None:
    if message_count % 100 == 0:
        await asyncio.sleep(2)