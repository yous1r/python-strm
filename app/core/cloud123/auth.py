from loguru import logger
from app.config import get_config
import httpx

class Cloud123Auth:
    def __init__(self):
        self.access_token = get_config().cloud123.access_token

    def update_token(self, token_str: str) -> bool:
        """更新token"""
        if not token_str:
            return False
        self.access_token = token_str
        
        # 简单验证token格式，实际可在这里加入接口验证
        config = get_config()
        config.cloud123.access_token = token_str
        logger.info("123pan token updated successfully.")
        return True

    async def get_user_info(self) -> dict:
        """获取用户信息"""
        if not self.access_token:
            return {"error": "Token not initialized"}
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Platform": "mac",
            "App-Version": "3.0.0"
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get("https://www.123pan.com/api/user/info", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("code") == 0:
                        user = data.get("data", {})
                        return {
                            "uid": user.get("uid"),
                            "name": user.get("nickname"),
                            "vip": user.get("vip"),
                        }
                return {"error": "Failed to fetch user info or token invalid"}
        except Exception as e:
            return {"error": str(e)}

# 单例
auth_manager_123 = Cloud123Auth()
