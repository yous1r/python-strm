import asyncio
import json
import re
import sqlite3
from loguru import logger
from telethon import events
from app.config import get_config
from app.core.monitor.telegram_runtime import (
    build_telegram_client,
    close_telegram_session_connection_without_commit,
    extract_message_text,
    extract_message_torrent_files,
    parse_channels,
    telegram_session_operation_lock,
)
from app.services.telegram_resource_transfer_service import normalize_resource_payload, process_resource_transfer
from app.utils.background_tasks import CLOUD_API_POOL, spawn_background_task

TELEGRAM_SESSION_RETRY_ATTEMPTS = 3
TELEGRAM_SESSION_RETRY_DELAY_SECONDS = 2.0


def _is_telegram_session_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


class TelegramMonitor:
    def __init__(self):
        self.config = get_config().monitor.telegram
        self.client = None
        self._link_priority = {"115": 0, "123": 1, "magnet": 2, "torrent": 3}

    async def start(self):
        """启动Telegram监听服务"""
        self.config = get_config().monitor.telegram

        if not self.config.enabled:
            return

        if not self.config.api_id or not self.config.api_hash:
            logger.error("Telegram API ID or Hash is missing.")
            return

        parsed_channels = parse_channels(self.config.channels or [])

        for attempt in range(1, TELEGRAM_SESSION_RETRY_ATTEMPTS + 1):
            should_retry = False
            async with telegram_session_operation_lock:
                if self.client and self.client.is_connected():
                    logger.info("Telegram monitor already connected.")
                    return

                self.client = self._build_client()
                self._attach_message_handler(parsed_channels)

                try:
                    await self.client.connect()
                    if not await self.client.is_user_authorized():
                        if getattr(self.config, 'bot_token', ''):
                            await self.client.start(bot_token=self.config.bot_token)
                        else:
                            logger.error("Telegram Monitor not authorized! Please run login_tg.py manually.")
                            await self.client.disconnect()
                            return
                    else:
                        await self.client.start()
                except Exception as exc:
                    await self._disconnect_current_client_safely()
                    if _is_telegram_session_locked(exc) and attempt < TELEGRAM_SESSION_RETRY_ATTEMPTS:
                        should_retry = True
                        logger.warning(
                            "Telegram session database is locked during startup; "
                            f"retrying {attempt}/{TELEGRAM_SESSION_RETRY_ATTEMPTS}"
                        )
                    else:
                        raise

            if should_retry:
                await asyncio.sleep(TELEGRAM_SESSION_RETRY_DELAY_SECONDS)
                continue
            break

        logger.info("Telegram monitor started.")

    def _build_client(self):
        try:
            return build_telegram_client(self.config.api_id, self.config.api_hash, self.config.proxy)
        except Exception as exc:
            logger.error(f"Failed to parse monitor proxy: {exc}")
            return build_telegram_client(self.config.api_id, self.config.api_hash)

    def _attach_message_handler(self, parsed_channels):
        @self.client.on(events.NewMessage(chats=parsed_channels))
        async def handler(event):
            """接收到新消息后的回调"""
            self.config = get_config().monitor.telegram
            text = extract_message_text(event.message)
            torrent_files = extract_message_torrent_files(event.message)

            # Keyword matching
            if self.config.keywords:
                valid_kws = [kw.strip().lower() for kw in self.config.keywords if kw.strip()]
                if valid_kws:
                    matched = any(kw in text.lower() for kw in valid_kws)
                    if not matched:
                        return

            channel_str = str(event.chat_id) if hasattr(event, 'chat_id') else None
            new_resources = await self.ingest_message(
                text,
                message_id=event.message.id,
                channel_id=channel_str,
                msg_date=str(event.message.date),
                torrent_files=torrent_files,
            )
            if new_resources:
                logger.info(f"Found and ingested {len(new_resources)} new Telegram resources")
                for link_data in new_resources:
                    # 实时监听只负责投递可观测后台任务，避免转存链路阻塞消息消费循环。
                    spawn_background_task(
                        lambda link_data=link_data: process_resource_transfer(link_data, source="telegram"),
                        name="telegram_resource_transfer",
                        pool=CLOUD_API_POOL,
                    )

        return handler

    async def _disconnect_current_client_safely(self):
        client = self.client
        self.client = None
        if not client:
            return
        try:
            await client.disconnect()
        except Exception as exc:
            logger.warning(f"Failed to disconnect Telegram client cleanly after startup error: {exc}")
            close_telegram_session_connection_without_commit(client, context="startup disconnect failure")

    async def stop(self):
        """停止监听"""
        if self.client:
            async with telegram_session_operation_lock:
                await self.client.disconnect()

    def _clean_url(self, url: str) -> str:
        return (url or "").strip().rstrip('.,);!>')

    def _extract_fallback_password(self, text: str) -> str:
        pwd_match = re.search(r'(?:码|密码|提取码|访问码)[:：\s]*([a-zA-Z0-9]{4})(?:\b|$)', text)
        return pwd_match.group(1) if pwd_match else ""

    def _detect_resource_type(self, url: str) -> str:
        normalized_url = self._clean_url(url)
        if re.match(r'^https?://115(?:cdn)?\.com/s/\w+(?:\?[^\s"\'<>]+)?$', normalized_url, re.IGNORECASE):
            return "115"
        if re.match(r'^https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html(?:\?[^\s"\'<>]+)?$', normalized_url, re.IGNORECASE):
            return "123"
        if normalized_url.lower().startswith("magnet:?"):
            return "magnet"
        return ""

    def _dedupe_link_items(self, links: list[dict]) -> list[dict]:
        unique_links = []
        seen = set()
        for link in links:
            key = (link.get("type", ""), link.get("url", ""), link.get("raw_url", ""))
            if key in seen:
                continue
            seen.add(key)
            unique_links.append(link)
        return unique_links

    def _normalize_torrent_files(self, torrent_files: list[dict] | None) -> list[dict]:
        normalized: list[dict] = []
        seen: set[tuple[str, str, int | None]] = set()
        for torrent_file in torrent_files or []:
            item = {
                "name": (torrent_file.get("name") or "unknown.torrent").strip(),
                "mime_type": (torrent_file.get("mime_type") or "").strip().lower(),
                "size": torrent_file.get("size"),
            }
            key = (item["name"], item["mime_type"], item["size"])
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def extract_links(self, text: str) -> list[dict]:
        links: list[dict] = []
        cleaned_text = text or ""
        fallback_password = self._extract_fallback_password(cleaned_text)

        link_matches_115 = re.findall(r'https?://115(?:cdn)?\.com/s/\w+(?:\?[^\s\"\'<]+)?', cleaned_text)
        for raw_url in link_matches_115:
            normalized_raw_url = self._clean_url(raw_url)
            password = fallback_password
            pwd_url_match = re.search(r'password=([a-zA-Z0-9]+)', normalized_raw_url)
            if pwd_url_match:
                password = pwd_url_match.group(1)
            clean_url = normalized_raw_url.split('?')[0] if '?' in normalized_raw_url else normalized_raw_url
            links.append({"url": clean_url, "raw_url": normalized_raw_url, "password": password, "type": "115"})

        pan123_matches = re.findall(r'https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html(?:\?[^\s\"\'<]+)?', cleaned_text)
        for raw_url in pan123_matches:
            normalized_raw_url = self._clean_url(raw_url)
            password = fallback_password
            pwd_url_match = re.search(r'Pwd=([a-zA-Z0-9]+)', normalized_raw_url, re.IGNORECASE)
            if pwd_url_match:
                password = pwd_url_match.group(1)
            clean_url = normalized_raw_url.split('?')[0] if '?' in normalized_raw_url else normalized_raw_url
            links.append({"url": clean_url, "raw_url": normalized_raw_url, "password": password, "type": "123"})

        for magnet in re.findall(r'magnet:\?[^\s\"\'<>]+', cleaned_text):
            normalized_magnet = self._clean_url(magnet)
            links.append({"url": normalized_magnet, "raw_url": normalized_magnet, "password": "", "type": "magnet"})

        return self._dedupe_link_items(links)

    def summarize_resources(self, text: str, torrent_files: list[dict] | None = None) -> dict | None:
        links = self.extract_links(text)
        normalized_torrent_files = self._normalize_torrent_files(torrent_files)
        if not links and not normalized_torrent_files:
            return None

        primary_link = None
        if links:
            primary_link = min(links, key=lambda item: self._link_priority.get(item.get("type", "url"), 999))

        if primary_link is not None:
            link_value = primary_link.get("url", "")
            password_value = primary_link.get("password", "")
            disk_type_value = primary_link.get("type", "url")
        else:
            first_torrent = normalized_torrent_files[0]
            link_value = first_torrent.get("name", "unknown.torrent")
            password_value = ""
            disk_type_value = "torrent"

        return {
            "link": link_value,
            "password": password_value,
            "disk_type": disk_type_value,
            "resource_links": links,
            "url_links": [item["raw_url"] for item in links if item.get("type") != "magnet"],
            "magnet_links": [item["url"] for item in links if item.get("type") == "magnet"],
            "torrent_files": normalized_torrent_files,
            "resource_count": len(links) + len(normalized_torrent_files),
        }

    async def ingest_message(
        self,
        text: str,
        message_id: int = None,
        channel_id: str = None,
        msg_date: str = None,
        torrent_files: list[dict] | None = None,
    ) -> list[dict]:
        """从文本和附件提取资源，提纯标题并按消息粒度入库。"""
        from app.core.monitor.parser import extract_title_from_text
        from app.database import get_db_conn, insert_tg_resource
        
        resource_summary = self.summarize_resources(text, torrent_files=torrent_files)
        if not resource_summary:
            return []
            
        title = extract_title_from_text(text)
        
        from guessit import guessit
        from app.core.tmdb.client import tmdb_client
        import asyncio
        
        guessed = guessit(title)
        base_title = guessed.get("title") or title
        year = str(guessed.get("year", ""))
        poster_url = None
        
        try:
            results = await tmdb_client.search_movie(base_title, year)
            if not results:
                results = await tmdb_client.search_tv(base_title, year)
            if results and results[0].get('poster_path'):
                poster_url = f"https://image.tmdb.org/t/p/w342{results[0]['poster_path']}"
        except Exception:
            pass

        new_resources = []
        
        async with get_db_conn() as db:
            resource = {
                "message_id": message_id,
                "channel_id": channel_id,
                "title": title,
                "raw_text": text,
                "link": resource_summary["link"],
                "password": resource_summary["password"],
                "disk_type": resource_summary["disk_type"],
                "msg_date": msg_date,
                "status": "pending",
                "base_title": base_title,
                "poster_url": poster_url,
                "resource_links": json.dumps(resource_summary["resource_links"], ensure_ascii=False),
                "url_links": json.dumps(resource_summary["url_links"], ensure_ascii=False),
                "magnet_links": json.dumps(resource_summary["magnet_links"], ensure_ascii=False),
                "torrent_files": json.dumps(resource_summary["torrent_files"], ensure_ascii=False),
                "resource_count": resource_summary["resource_count"],
            }
            inserted_resource = await insert_tg_resource(db, resource)
            if inserted_resource:
                new_resources.append(normalize_resource_payload(inserted_resource))
            await db.commit()
            
        return new_resources

telegram_monitor = TelegramMonitor()
