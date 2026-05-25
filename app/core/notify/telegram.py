import httpx
from loguru import logger
from app.config import get_config

class TelegramNotifier:
    def __init__(self):
        self.config = get_config().notify.telegram

    async def send_message(self, content: str, title: str = "系统通知"):
        if not self.config.enabled:
            return
        if not self.config.bot_token or not self.config.chat_id:
            logger.error("Telegram notify is enabled but bot_token or chat_id is missing.")
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": f"*{title}*\n\n{content}",
            "parse_mode": "Markdown"
        }
        
        try:
            client_kwargs = {"timeout": 10}
            if self.config.proxy:
                client_kwargs["proxy"] = self.config.proxy
                
            async with httpx.AsyncClient(**client_kwargs) as client:
                res = await client.post(url, json=payload)
                data = res.json()
                if not data.get("ok"):
                    logger.error(f"Telegram notify failed: {data.get('description')}")
        except Exception as e:
            logger.error(f"Telegram notify error: {e}")

notifier = TelegramNotifier()
