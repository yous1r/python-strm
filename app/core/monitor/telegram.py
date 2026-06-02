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

        self.client = TelegramClient('session_strm', self.config.api_id, self.config.api_hash, **client_kwargs)
        
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

            links = self.extract_links(text)
            if links:
                logger.info(f"Found new links in telegram: {links}")
                for link_data in links:
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
        # Find all 115 links
        link_matches_115 = re.findall(r'https?://115\.com/s/\w+', text)
        if link_matches_115:
            pwd_match = re.search(r'(?:码|密码|提取码|访问码)[:：\s]*([a-zA-Z0-9]{4})(?:\b|$)', text)
            password = pwd_match.group(1) if pwd_match else ""
            for url in link_matches_115:
                links.append({"url": url, "password": password, "type": "115"})
                
        # Also preserve 123pan
        pan123_matches = re.findall(r'https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html', text)
        if pan123_matches:
            pwd_match = re.search(r'(?:码|密码|提取码|访问码)[:：\s]*([a-zA-Z0-9]{4})(?:\b|$)', text)
            password = pwd_match.group(1) if pwd_match else ""
            for url in pan123_matches:
                links.append({"url": url, "password": password, "type": "123"})
            
        # Deduplicate
        unique_links = []
        seen = set()
        for link in links:
            if link["url"] not in seen:
                seen.add(link["url"])
                unique_links.append(link)
        return unique_links

telegram_monitor = TelegramMonitor()
