import asyncio
import sys
import urllib.parse
from telethon import TelegramClient
from app.config import get_config

async def main():
    config = get_config().monitor.telegram
    if not config.api_id or not config.api_hash:
        print("❌ 错误：请先在前端页面配置并保存 API ID 和 API Hash！")
        sys.exit(1)

    client_kwargs = {}
    if config.proxy:
        proxy_str = config.proxy
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
        print(f"🌍 使用代理: {proxy_type}://{parsed.hostname}:{parsed.port}")

    print("🚀 正在连接 Telegram 服务器...")
    client = TelegramClient('session_strm', config.api_id, config.api_hash, **client_kwargs)
    
    # client.start() 会在未登录时在控制台要求输入手机号和验证码
    await client.start()
    
    me = await client.get_me()
    print(f"✅ 登录成功！当前账号: {me.first_name} (@{me.username})")
    print("🎉 session_strm.session 文件已成功生成！现在您可以去网页端点击【测试连通性】了。")

if __name__ == "__main__":
    asyncio.run(main())
