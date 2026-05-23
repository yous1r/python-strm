from p115client import P115Client
import tempfile
import os
from loguru import logger
from app.config import get_config

class Cloud115Auth:
    def __init__(self):
        self.client = None
        self.cookie = get_config().cloud115.cookie
        # 使用临时文件存储cookie给p115client用
        self.cookie_file = os.path.join(tempfile.gettempdir(), "115_cookies.txt")
        self._init_client()

    def _init_client(self):
        """初始化p115client"""
        if self.cookie:
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                f.write(self.cookie)
            try:
                # 安全解析 cookie_str
                cookie_dict = {}
                for part in self.cookie.split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookie_dict[k] = v
                        
                # 尝试加载，必须保持 app="alipaymini" 以防令牌平台不一致触发封控
                self.client = P115Client(cookie_dict, app="alipaymini", check_for_relogin=True)
                logger.info("115 Client initialized with existing cookie.")
            except Exception as e:
                logger.error(f"Failed to initialize 115 Client: {e}")
                self.client = None

    def update_cookie(self, cookie_str: str) -> bool:
        """更新cookie"""
        if not cookie_str:
            return False
            
        self.cookie = cookie_str
        with open(self.cookie_file, "w", encoding="utf-8") as f:
            f.write(self.cookie)
        try:
            # 安全解析 cookie_str
            cookie_dict = {}
            for part in self.cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookie_dict[k] = v
                    
            # 必须保持 app="alipaymini" 防止风控作废 cookie
            self.client = P115Client(cookie_dict, app="alipaymini", check_for_relogin=True)
            # 保存回配置
            from app.config import update_config
            update_config({"cloud115": {"cookie": cookie_str}})
            logger.info("115 cookie updated and persisted to config.yaml successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to update 115 cookie: {e}")
            return False

    async def validate_cookie(self) -> bool:
        """验证cookie是否有效"""
        if not self.client:
            return False
        try:
            # 调用一个简单的接口验证
            res = await self.client.user_info(async_=True)
            if res.get("state"):
                return True
            return False
        except Exception as e:
            logger.warning(f"115 cookie validation failed: {e}")
            return False

    async def get_user_info(self) -> dict:
        """获取用户信息"""
        if not self.client:
            return {"error": "Client not initialized"}
        try:
            res = await self.client.user_info(async_=True)
            if res.get("state"):
                data = res.get("data", {})
                return {
                    "uid": data.get("user_id"),
                    "name": data.get("user_name"),
                    "vip": data.get("is_vip"),
                    "level": data.get("vip_level")
                }
            return {"error": res.get("error", "Unknown error")}
        except Exception as e:
            return {"error": str(e)}

    async def get_qr_token(self) -> dict:
        """获取二维码信息"""
        try:
            import asyncio
            # 直接调用静态方法获取token，使用 alipaymini 避免 web 端容易触发的 IP 异常
            res = await asyncio.to_thread(P115Client.login_qrcode_token, app="alipaymini", async_=False)
            if res.get("state"):
                return res.get("data", {})
            logger.error(f"Get QR token failed: {res}")
            return {"error": res.get("message", "Unknown error")}
        except Exception as e:
            logger.error(f"Get QR token error: {e}")
            return {"error": str(e)}

    async def check_qr_status(self, payload: dict) -> dict:
        """检查扫码状态并自动处理登录"""
        try:
            import asyncio
            # 检查状态
            status_res = await asyncio.to_thread(P115Client.login_qrcode_scan_status, payload, async_=False)
            status_data = status_res.get("data", {})
            
            # status_data 中的 status: 
            # 0: 等待扫码
            # 1: 扫码成功，等待确认
            # 2: 登录成功
            # -1: 二维码过期或取消
            status = status_data.get("status")
            msg = status_data.get("msg", "")
            
            if status == 2:
                # 登录成功，获取结果(cookie)
                uid = payload.get("uid")
                temp_client = P115Client("")
                # 使用 alipaymini 获取 cookie
                login_res = await asyncio.to_thread(temp_client.login_qrcode_scan_result, uid, app="alipaymini", async_=False)
                if login_res.get("state"):
                    # 获取返回的 cookie
                    cookie_str = temp_client.cookies_str
                    if not cookie_str:
                        cookie_data = login_res.get("data", {}).get("cookie", {})
                        if cookie_data:
                            cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_data.items()])
                    if cookie_str:
                        # 更新当前认证
                        self.update_cookie(cookie_str)
                        return {"status": 2, "msg": "登录成功", "cookie": cookie_str}
                
                error_msg = login_res.get("message", "未知错误") if isinstance(login_res, dict) else "未知错误"
                logger.error(f"Failed to get login result: {login_res}")
                return {"status": -2, "msg": f"登录失败: {error_msg}"}
            
            return {"status": status, "msg": msg}
        except Exception as e:
            logger.error(f"Check QR status error: {e}")
            return {"error": str(e)}

# 单例
auth_manager = Cloud115Auth()
