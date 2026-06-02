from fastapi import APIRouter, Request, HTTPException
from app.config import get_config, update_config
from pydantic import BaseModel
from typing import List, Optional

class TelegramTestRequest(BaseModel):
    api_id: str
    api_hash: str
    bot_token: str = ""
    proxy: str = ""
    channels: List[str] = []

router = APIRouter(prefix="/system", tags=["System Config"])

@router.get("/config")
async def fetch_config():
    """获取当前系统的完整配置"""
    return get_config().model_dump()

@router.patch("/config")
async def modify_config(request: Request):
    """增量热更新配置"""
    try:
        data = await request.json()
        
        # Check if emby proxy config was updated
        old_config = get_config()
        old_proxy_enabled = old_config.emby.proxy.enabled
        old_instances = {(i.name, i.proxy_port, i.url) for i in old_config.emby.proxy.instances}
        
        new_config = update_config(data)
        
        # Handle hot reload for proxy (detect any instance config change)
        if 'emby' in data and 'proxy' in data['emby']:
            from app.core.emby.standalone_proxy import restart_standalone_proxy
            import asyncio
            new_proxy_enabled = new_config.emby.proxy.enabled
            new_instances = {(i.name, i.proxy_port, i.url) for i in new_config.emby.proxy.instances}
            if old_proxy_enabled != new_proxy_enabled or old_instances != new_instances:
                asyncio.create_task(restart_standalone_proxy())
                
        return {"status": "success", "config": new_config.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置更新失败或格式校验不通过: {str(e)}")

@router.post("/test-notify/{channel}")
async def test_notify(channel: str):
    """测试发送通知 (channel = wecom / telegram / bark / all)"""
    from datetime import datetime
    from app.core.notify.wecom import notifier as wecom_notifier
    from app.core.notify.telegram import notifier as telegram_notifier
    from app.core.notify.bark import notifier as bark_notifier
    from app.core.notify.manager import notify_manager
    
    title = "✅ Python-STRM 测试通知"
    content = f"这是一条来自 {channel} 通道的测试消息，证明您的配置完全正确！\n时间戳：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    try:
        if channel == "wecom":
            await wecom_notifier.send_message(content, title)
        elif channel == "telegram":
            await telegram_notifier.send_message(content, title)
        elif channel == "bark":
            await bark_notifier.send_message(content, title)
        elif channel == "all":
            await notify_manager.notify(title, content)
        else:
            raise HTTPException(status_code=400, detail="未知的推送通道")
            
        return {"status": "success", "message": "测试请求已触发"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/test-monitor/telegram")
async def test_telegram_monitor(req: TelegramTestRequest):
    """测试 Telegram 监控节点登录状态并获取第一条测试消息"""
    try:
        from app.core.monitor.telegram import telegram_monitor
        if not req.api_id or not req.api_hash:
            raise HTTPException(status_code=400, detail="API ID 和 API Hash 不能为空")
            
        if not req.channels:
            raise HTTPException(status_code=400, detail="未配置频道！请在前端添加至少一个监听频道后再试。")
            
        import re
        first_channel = req.channels[0].strip()
        parsed_channel = first_channel
        match_c = re.search(r't\.me/c/(\d+)', first_channel)
        if match_c:
            parsed_channel = int(f"-100{match_c.group(1)}")
        else:
            match_u = re.search(r't\.me/([a-zA-Z0-9_]+)', first_channel)
            if match_u and match_u.group(1) not in ['c', 'joinchat', 'setlanguage']:
                parsed_channel = match_u.group(1)
            elif first_channel.startswith('@'):
                parsed_channel = first_channel[1:]
            else:
                try:
                    parsed_channel = int(first_channel)
                except ValueError:
                    pass

        is_auth = False
        client_to_use = None
        disconnect_after = False
        
        if telegram_monitor.client and telegram_monitor.client.is_connected():
            is_auth = await telegram_monitor.client.is_user_authorized()
            client_to_use = telegram_monitor.client
        else:
            from telethon import TelegramClient
            import urllib.parse
            
            client_kwargs = {}
            if req.proxy:
                proxy_str = req.proxy
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

            client_to_use = TelegramClient('session_strm', req.api_id, req.api_hash, **client_kwargs)
            await client_to_use.connect()
            disconnect_after = True
            
            if not await client_to_use.is_user_authorized():
                if req.bot_token:
                    try:
                        await client_to_use.start(bot_token=req.bot_token)
                        is_auth = True
                    except Exception as e:
                        await client_to_use.disconnect()
                        return {"status": "error", "message": f"Bot Token 登录失败: {str(e)}"}
                else:
                    is_auth = False
            else:
                is_auth = True
        
        if not is_auth:
            if disconnect_after:
                await client_to_use.disconnect()
            return {"status": "error", "message": "连接成功，但尚未登录。请填写 Bot Token 或在后端运行 python login_tg.py 完成扫码登录。"}
            
        # 测试读取第一个频道
        try:
            messages = await client_to_use.get_messages(parsed_channel, limit=1)
            msg_text = messages[0].text if messages and messages[0].text else "[图片/非文本消息或空消息]"
            success_msg = f"连接并鉴权成功！\n成功读取到频道 [{first_channel}] 的最新一条消息：\n\n{msg_text}"
        except Exception as e:
            success_msg = f"连接并鉴权成功！但读取频道 [{first_channel}] 失败，可能您还未加入该频道，或者权限不足。\n错误信息: {str(e)}"

        if disconnect_after:
            await client_to_use.disconnect()
            
        return {"status": "success", "message": success_msg}
            
    except Exception as e:
        return {"status": "error", "message": f"测试失败: {str(e)}"}

@router.post("/test-emby")
async def test_emby(request: Request):
    """测试 Emby 连接"""
    try:
        data = await request.json()
        url = data.get("url", "").rstrip("/")
        api_key = data.get("api_key", "")
        
        if not url:
            raise HTTPException(status_code=400, detail="地址不能为空")
            
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            params = {"api_key": api_key} if api_key else {}
            # Try to fetch public info
            res = await client.get(f"{url}/emby/system/info/public", params=params)
            if res.status_code != 200:
                res = await client.get(f"{url}/system/info/public", params=params)
                
            if res.status_code == 200:
                info = res.json()
                msg = f"连接成功！服务器版本: {info.get('Version')}"
                if not api_key:
                    msg += " (未配置API Key，代理仍可正常工作)"
                return {"status": "success", "message": msg}
            else:
                return {"status": "error", "message": f"连接失败: 状态码 {res.status_code}"}
    except Exception as e:
        return {"status": "error", "message": f"连接异常: {str(e)}"}

@router.post("/test-tmdb")
async def test_tmdb():
    """测试 TMDB 连通性"""
    from app.core.tmdb.client import tmdb_client
    try:
        # Search a popular movie to test
        res = await tmdb_client.search_movie("Inception")
        if res:
            return {"status": "success", "message": "TMDB 连通性测试成功！获取到数据。"}
        else:
            return {"status": "warning", "message": "连通正常，但未搜索到结果，请检查 API Key。"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TMDB 连接失败: {str(e)}")

@router.post("/sync/run")
async def trigger_sync_now(force: bool = False):
    """立即在后台触发一次全量自动化同步任务"""
    from app.core.sync.engine import sync_engine
    import asyncio
    # 放进后台任务执行，不阻塞当前的 API 请求
    asyncio.create_task(sync_engine.run_sync_task(force=force))
    msg = "强制全自动同步任务已在后台触发" if force else "全自动增量同步任务已在后台触发"
    return {"status": "success", "message": msg}

@router.get("/sync/history")
async def fetch_sync_history():
    """获取最近的 50 条自动化同步流水"""
    from app.database import get_db_conn
    try:
        async with get_db_conn() as db:
            async with db.execute(
                "SELECT id, task_name, status, duration, processed_count, error_details, created_at FROM sync_history ORDER BY id DESC LIMIT 50"
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row["id"],
                        "task_name": row["task_name"],
                        "status": row["status"],
                        "duration": round(row["duration"], 2) if row["duration"] else 0,
                        "processed_count": row["processed_count"],
                        "error_details": row["error_details"],
                        "created_at": row["created_at"]
                    })
                return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
