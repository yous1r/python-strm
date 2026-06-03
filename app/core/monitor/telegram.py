import asyncio
import re
from loguru import logger
from telethon import TelegramClient, events
from app.config import get_config
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
        if not self.config.enabled:
            return

        if not self.config.api_id or not self.config.api_hash:
            logger.error("Telegram API ID or Hash is missing.")
            return

        client_kwargs = {}
        if self.config.proxy:
            import urllib.parse
            try:
                proxy_str = self.config.proxy
                if not proxy_str.startswith(("http://", "https://", "socks5://", "socks5h://")):
                    proxy_str = f"http://{proxy_str}"
                parsed = urllib.parse.urlparse(proxy_str)
                proxy_type = parsed.scheme.lower()
                if proxy_type in ["http", "https"]:
                    proxy_type = "http"
                elif proxy_type in ["socks5", "socks5h"]:
                    proxy_type = "socks5"
                client_kwargs["proxy"] = {
                    "proxy_type": proxy_type,
                    "addr": parsed.hostname,
                    "port": parsed.port
                }
            except Exception as e:
                logger.error(f"Failed to parse monitor proxy: {e}")

        import os
        os.makedirs('data', exist_ok=True)
        self.client = TelegramClient('data/session_strm', self.config.api_id, self.config.api_hash, **client_kwargs)
        
        parsed_channels = []
        for ch in (self.config.channels or []):
            ch = ch.strip()
            if not ch: continue
            
            # https://t.me/c/1234567890/123 -> -1001234567890
            match_c = re.search(r't\.me/c/(\d+)', ch)
            if match_c:
                parsed_channels.append(int(f"-100{match_c.group(1)}"))
                continue
                
            # https://t.me/username or @username
            match_u = re.search(r't\.me/([a-zA-Z0-9_]+)', ch)
            if match_u and match_u.group(1) not in ['c', 'joinchat', 'setlanguage']:
                parsed_channels.append(match_u.group(1))
                continue
                
            if ch.startswith('@'):
                parsed_channels.append(ch[1:])
                continue
                
            # Try to convert to int (like -100... or just digits)
            try:
                parsed_channels.append(int(ch))
            except ValueError:
                parsed_channels.append(ch)
        
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
                    # 只针对实时消息，立刻推送转存队列，并在 handler 中将数据库状态更新为 queued
                    await event_bus.emit(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')

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
        
        import PTN
        from app.core.tmdb.client import tmdb_client
        import asyncio
        
        parsed = PTN.parse(title)
        base_title = parsed.get("title") or title
        year = parsed.get("year", "")
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
