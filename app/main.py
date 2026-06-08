from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import os

from app.config import get_config
from app.database import init_db
from app.utils.logger import setup_logger
from app.utils.scheduler import start_scheduler, stop_scheduler, add_job

# 初始化日志
logger = setup_logger()

from app.core.monitor.telegram import telegram_monitor
from app.core.monitor.handler import init_handlers
from app.core.cloud115.db_sync import init_db_sync_events
from app.services.cloud115_full_sync_service import (
    cloud115_full_sync_service,
    init_cloud115_full_sync_events,
)
from app.services.telegram_background_service import telegram_background_sync_service
from app.core.sync.engine import sync_engine

from app.core.emby.standalone_proxy import restart_standalone_proxy, stop_standalone_proxy
from app.core.transfer import init_transfer_pipeline
from app.events import spawn_task
from app.services.startup_bootstrap_service import run_startup_pipeline

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    logger.info("Starting Python-STRM application...")
    await init_db()
    start_scheduler()
    
    init_handlers()
    init_db_sync_events()
    init_cloud115_full_sync_events()
    init_transfer_pipeline()

    config = get_config()
    
    # 注册核心自动化同步任务
    interval_mins = config.monitor.poll_interval if getattr(config.monitor, 'poll_interval', None) else 60
    add_job(sync_engine.run_sync_task, "interval", minutes=interval_mins, id="auto_sync", replace_existing=True)

    # 注册 115 全链路同步任务（每 2 小时）
    add_job(
        cloud115_full_sync_service.run_scheduled_full_sync,
        "interval",
        hours=2,
        id="db_sync",
        replace_existing=True,
    )
    logger.info("Registered 115 full sync job (every 2h)")

    telegram_background_sync_service.configure_scheduled_sync_job()

    if config.monitor.startup_pipeline.enabled:
        spawn_task(run_startup_pipeline(), name="startup_pipeline")

    if config.monitor.telegram.enabled:
        spawn_task(telegram_monitor.start(), name="telegram_monitor")
        
    spawn_task(restart_standalone_proxy(), name="standalone_proxy")
        
    yield
    # 关闭时执行
    logger.info("Shutting down Python-STRM application...")
    await stop_standalone_proxy()
    stop_scheduler()
    if config.monitor.telegram.enabled:
        await telegram_monitor.stop()

app = FastAPI(
    title="Python-STRM 影视管理平台",
    description="115网盘影视库自动化管理平台",
    version="1.0.0",
    lifespan=lifespan
)

# 挂载静态文件
os.makedirs("app/web/static", exist_ok=True)
from app.api import cloud115, cloud123, strm, organize, search, web, system, transfer
from app.api.debug import router as debug_router

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

templates = Jinja2Templates(directory="app/web/templates")

@app.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return templates.TemplateResponse(request, "library.html", {"title": "资源图鉴"})

# 注册各类路由
app.include_router(cloud115.router)
app.include_router(cloud123.router)
app.include_router(strm.router)
app.include_router(organize.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
from app.api.library import router as library_router
app.include_router(library_router, prefix="/api/v1")
app.include_router(transfer.router)
app.include_router(debug_router)
app.include_router(web.router)

if __name__ == "__main__":
    import uvicorn
    config = get_config()
    uvicorn.run("app.main:app", host=config.server.host, port=config.server.port, reload=config.server.debug)
