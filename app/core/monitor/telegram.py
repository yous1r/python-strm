import asyncio
import re
from loguru import logger
from telethon import TelegramClient, events
from app.config import get_config
from app.core.monitor.telegram_runtime import build_telegram_client, parse_channels
from app.events import event_bus, EVENT_MONITOR_NEW_LINK

class TelegramMonitor:
    def __init__(self):
        self.config = get_config().monitor.telegram
        self.client = None
        self.link_patterns = [
            re.compile(r'https?://115\.com/s/\w+'),
            re.compile(r'https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html')
        ]

    async def start(self):
        """启动Telegram监听服务"""
        self.config = get_config().monitor.telegram

        if not self.config.enabled:
            return

        if not self.config.api_id or not self.config.api_hash:
            logger.error("Telegram API ID or Hash is missing.")
            return

        try:
            self.client = build_telegram_client(self.config.api_id, self.config.api_hash, self.config.proxy)
        except Exception as exc:
            logger.error(f"Failed to parse monitor proxy: {exc}")
            self.client = build_telegram_client(self.config.api_id, self.config.api_hash)

        parsed_channels = parse_channels(self.config.channels or [])
        
        @self.client.on(events.NewMessage(chats=parsed_channels))
        async def handler(event):
            self.config = get_config().monitor.telegram
            text = event.message.message or ""
            
            # Keyword matching
            if self.config.keywords:
                valid_kws = [kw.strip().lower() for kw in self.config.keywords if kw.strip()]
                if valid_kws:
                    matched = any(kw in text.lower() for kw in valid_kws)
                    if not matched:
                        return

            channel_str = str(event.chat_id) if hasattr(event, 'chat_id') else None
            new_links = await self.ingest_message(
                text, 
                message_id=event.message.id,
                channel_id=channel_str,
                msg_date=str(event.message.date)
            )
            if new_links:
                logger.info(f"Found and ingested new links: {new_links}")
                for link_data in new_links:
                    # 实时监听只负责投递事件，避免转存链路阻塞消息消费循环。
                    event_bus.emit_background(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')

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

        logger.info("Telegram monitor started.")

    async def stop(self):
        """停止监听"""
        if self.client:
            await self.client.disconnect()

    def extract_links(self, text: str) -> list:
        links = []
        # Find all 115 links (including 115cdn.com)
        # We need to capture the full URL to extract the password from query string if present
        link_matches_115 = re.findall(r'https?://115(?:cdn)?\.com/s/\w+(?:\?[^\s\"\'<]+)?', text)
        if link_matches_115:
            pwd_match = re.search(r'(?:码|密码|提取码|访问码)[:：\s]*([a-zA-Z0-9]{4})(?:\b|$)', text)
            fallback_password = pwd_match.group(1) if pwd_match else ""
            for url in link_matches_115:
                password = fallback_password
                pwd_url_match = re.search(r'password=([a-zA-Z0-9]+)', url)
                if pwd_url_match:
                    password = pwd_url_match.group(1)
                
                # Clean URL (remove query params for canonical url if desired, or keep them)
                clean_url = url.split('?')[0] if '?' in url else url
                links.append({"url": clean_url, "password": password, "type": "115"})
                
        # Also preserve 123pan
        pan123_matches = re.findall(r'https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html(?:\?[^\s\"\'<]+)?', text)
        if pan123_matches:
            pwd_match = re.search(r'(?:码|密码|提取码|访问码)[:：\s]*([a-zA-Z0-9]{4})(?:\b|$)', text)
            fallback_password = pwd_match.group(1) if pwd_match else ""
            for url in pan123_matches:
                password = fallback_password
                pwd_url_match = re.search(r'Pwd=([a-zA-Z0-9]+)', url, re.IGNORECASE)
                if pwd_url_match:
                    password = pwd_url_match.group(1)
                
                clean_url = url.split('?')[0] if '?' in url else url
                links.append({"url": clean_url, "password": password, "type": "123"})
            
        # Deduplicate
        unique_links = []
        seen = set()
        for link in links:
            if link["url"] not in seen:
                seen.add(link["url"])
                unique_links.append(link)
        return unique_links

    async def ingest_message(self, text: str, message_id: int = None, channel_id: str = None, msg_date: str = None) -> list:
        """从文本提取链接，提纯标题并入库。返回成功入库的新链接信息。"""
        from app.core.monitor.parser import extract_title_from_text
        from app.database import get_db_conn, insert_tg_resource
        
        links = self.extract_links(text)
        if not links:
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

        new_links = []
        
        async with get_db_conn() as db:
            for link_data in links:
                resource = {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "title": title,
                    "raw_text": text,
                    "link": link_data["url"],
                    "password": link_data["password"],
                    "disk_type": link_data["type"],
                    "msg_date": msg_date,
                    "status": "pending",
                    "base_title": base_title,
                    "poster_url": poster_url
                }
                is_new = await insert_tg_resource(db, resource)
                if is_new:
                    new_links.append(link_data)
            await db.commit()
            
        return new_links

telegram_monitor = TelegramMonitor()
