import httpx
from loguru import logger
from app.config import get_config

class WeComNotifier:
    def __init__(self):
        self.config = get_config().notify.wecom
        self.access_token = None

    async def _get_access_token(self) -> str:
        """获取微信接口调用凭证"""
        if not self.config.corp_id or not self.config.corp_secret:
            return None
            
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.config.corp_id}&corpsecret={self.config.corp_secret}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url)
                data = res.json()
                if data.get("errcode") == 0:
                    self.access_token = data.get("access_token")
                    return self.access_token
                logger.error(f"WeCom token fetch failed: {data.get('errmsg')}")
        except Exception as e:
            logger.error(f"WeCom token fetch error: {e}")
        return None

    async def send_message(self, content: str, title: str = "系统通知"):
        """发送企业微信消息"""
        if not self.config.enabled:
            return
            
        token = await self._get_access_token()
        if not token:
            return
            
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": "@all",
            "msgtype": "text",
            "agentid": self.config.agent_id,
            "text": {
                "content": f"【{title}】\n{content}"
            },
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 0,
            "duplicate_check_interval": 1800
        }
        
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=payload)
                data = res.json()
                if data.get("errcode") != 0:
                    logger.error(f"WeCom send message failed: {data.get('errmsg')}")
        except Exception as e:
            logger.error(f"WeCom send message error: {e}")

notifier = WeComNotifier()
