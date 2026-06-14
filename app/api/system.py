from fastapi import APIRouter, Request, HTTPException
from app.config import get_config, update_config
from app.events import task_tracker
from pydantic import BaseModel
from typing import List

from app.services.system_service import (
    apply_runtime_config_changes,
    trigger_db_sync_task,
    trigger_emby_preheat_task,
    trigger_sync_task,
)
from app.services.telegram_service import (
    TelegramValidationError,
    get_monitor_status,
    restart_monitor,
    test_monitor_connection,
    validate_monitor_request,
)
from app.services.telegram_history_sync_service import (
    TelegramHistorySyncRequest,
    telegram_history_sync_service,
)

class TelegramTestRequest(BaseModel):
    api_id: str
    api_hash: str
    bot_token: str = ""
    proxy: str = ""
    channels: List[str] = []

class TelegramScrapeRequest(BaseModel):
    api_id: str
    api_hash: str
    bot_token: str = ""
    proxy: str = ""
    channels: List[str] = []
    keywords: List[str] = []
    mode: str = "relative_range"
    relative_value: int = 6
    relative_unit: str = "months"
    date_start: str = ""
    date_end: str = ""
    chunk_days: int = 7
    emit_events: bool = False


class TelegramSyncChannelRequest(TelegramScrapeRequest):
    channel_ref: str


class EmbyPreheatRequest(BaseModel):
    instance_name: str = ""
    user_id: str = ""
    library_ids: List[str] = []
    limit: int = 0
    overwrite: bool = False


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
        old_config = get_config()
        new_config = update_config(data)

        apply_runtime_config_changes(data, old_config, new_config)
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
            sent = await bark_notifier.send_message(content, title)
            if sent is False:
                raise HTTPException(
                    status_code=502,
                    detail="Bark 测试通知发送失败，请检查 Bark 服务地址、设备 Key、加密配置或服务响应",
                )
        elif channel == "all":
            await notify_manager.notify(title, content)
        else:
            raise HTTPException(status_code=400, detail="未知的推送通道")
            
        return {"status": "success", "message": "测试请求已触发"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/test-monitor/telegram")
async def test_telegram_monitor(req: TelegramTestRequest):
    """测试 Telegram 监控节点登录状态并获取第一条测试消息"""
    try:
        return await test_monitor_connection(
            req.api_id,
            req.api_hash,
            bot_token=req.bot_token,
            proxy=req.proxy,
            channels=req.channels,
        )
    except TelegramValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as e:
        return {"status": "error", "message": f"测试失败: {str(e)}"}

def _build_history_sync_request(req: TelegramScrapeRequest, *, source: str) -> TelegramHistorySyncRequest:
    return TelegramHistorySyncRequest(
        api_id=req.api_id,
        api_hash=req.api_hash,
        bot_token=req.bot_token,
        proxy=req.proxy,
        channels=req.channels,
        keywords=req.keywords,
        mode=req.mode,
        relative_value=req.relative_value,
        relative_unit=req.relative_unit,
        date_start=req.date_start,
        date_end=req.date_end,
        chunk_days=req.chunk_days,
        emit_events=req.emit_events,
        source=source,
    )


@router.post("/scrape-monitor/telegram")
async def scrape_telegram_monitor(req: TelegramScrapeRequest):
    """兼容旧入口：投递 Telegram 历史同步任务。"""
    try:
        validate_monitor_request(
            req.api_id,
            req.api_hash,
            req.channels,
            empty_channels_message="未配置任何监听频道，无法抓取",
        )
    except TelegramValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return telegram_history_sync_service.queue_history_sync_request(
        _build_history_sync_request(req, source="manual"),
        name="telegram_history_sync:manual",
    )


@router.get("/telegram/status")
async def telegram_status():
    return await get_monitor_status()


@router.post("/telegram/restart")
async def restart_telegram_monitor():
    await restart_monitor()
    return {"status": "success", "message": "Telegram 监听器已重启"}


@router.post("/telegram/sync")
async def sync_telegram_monitor(req: TelegramScrapeRequest):
    try:
        return telegram_history_sync_service.queue_history_sync_request(
            _build_history_sync_request(req, source="manual"),
            name="telegram_history_sync:manual",
        )
    except TelegramValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/telegram/history-sync")
async def history_sync_telegram_monitor(req: TelegramScrapeRequest):
    try:
        validate_monitor_request(
            req.api_id,
            req.api_hash,
            req.channels,
            empty_channels_message="未配置任何监听频道，无法抓取",
        )
        return telegram_history_sync_service.queue_history_sync_request(
            _build_history_sync_request(req, source="manual"),
            name="telegram_history_sync:manual",
        )
    except TelegramValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/telegram/sync-channel")
async def sync_telegram_monitor_channel(req: TelegramSyncChannelRequest):
    try:
        return telegram_history_sync_service.queue_history_sync_request(
            _build_history_sync_request(
                TelegramScrapeRequest(
                    api_id=req.api_id,
                    api_hash=req.api_hash,
                    bot_token=req.bot_token,
                    proxy=req.proxy,
                    channels=[req.channel_ref],
                    keywords=req.keywords,
                    mode=req.mode,
                    relative_value=req.relative_value,
                    relative_unit=req.relative_unit,
                    date_start=req.date_start,
                    date_end=req.date_end,
                    chunk_days=req.chunk_days,
                    emit_events=req.emit_events,
                ),
                source="manual",
            ),
            name=f"telegram_history_sync:channel:{req.channel_ref}",
        )
    except TelegramValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
    return trigger_sync_task(force=force)

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

@router.post("/db_sync")
async def trigger_db_sync():
    """手动触发 115 目录树本地缓存同步"""
    return trigger_db_sync_task()


@router.post("/emby/preheat-media-links")
async def trigger_emby_preheat(req: EmbyPreheatRequest):
    """手动触发 Emby 媒体项到 115 播放索引的批量预热"""
    return trigger_emby_preheat_task(
        instance_name=req.instance_name,
        user_id=req.user_id,
        library_ids=req.library_ids,
        limit=req.limit,
        overwrite=req.overwrite,
    )

@router.get("/tasks")
async def get_background_tasks():
    """观测所有后台任务（async + thread），返回活跃和最近完成的任务列表"""
    tasks = await task_tracker.get_active()
    summary = await task_tracker.get_summary()
    return {
        "status": "success",
        "summary": summary,
        "tasks": tasks
    }
