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
