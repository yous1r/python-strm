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
        
        @self.client.on(events.NewMessage(chats=self.config.channels))
        async def handler(event):
            text = event.message.message
            links = self.extract_links(text)
            if links:
                logger.info(f"Found new links in telegram: {links}")
                for link in links:
                    await event_bus.emit(EVENT_MONITOR_NEW_LINK, link=link, source='telegram')

        await self.client.start()
        logger.info("Telegram monitor started.")

    async def stop(self):
        """停止监听"""
        if self.client:
            await self.client.disconnect()

    def extract_links(self, text: str) -> list:
        links = []
        for pattern in self.link_patterns:
            matches = pattern.findall(text)
            links.extend(matches)
        return list(set(links))

telegram_monitor = TelegramMonitor()
