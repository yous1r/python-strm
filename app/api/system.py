from fastapi import APIRouter, Request, HTTPException
from app.config import get_config, update_config

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
        new_config = update_config(data)
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
