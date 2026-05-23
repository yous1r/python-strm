import asyncio
from loguru import logger
from app.core.notify.wecom import notifier as wecom_notifier
from app.core.notify.telegram import notifier as telegram_notifier
from app.core.notify.bark import notifier as bark_notifier

class NotificationManager:
    def __init__(self):
        self.notifiers = [
            wecom_notifier,
            telegram_notifier,
            bark_notifier
        ]

    async def notify(self, title: str, content: str):
        """并发向所有启用的通知渠道发送消息"""
        tasks = []
        for n in self.notifiers:
            tasks.append(n.send_message(content, title))
            
        if not tasks:
            return

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"NotificationManager dispatch error: {e}")

notify_manager = NotificationManager()
