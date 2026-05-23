from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import os

from app.config import get_config
from app.database import init_db
from app.utils.logger import setup_logger
from app.utils.scheduler import start_scheduler, stop_scheduler, add_job

# 初始化日志
logger = setup_logger()

from app.core.monitor.telegram import telegram_monitor
from app.core.sync.engine import sync_engine
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    logger.info("Starting Python-STRM application...")
    await init_db()
    start_scheduler()

    config = get_config()
    
    # 注册核心自动化同步任务
    interval_mins = config.monitor.poll_interval if getattr(config.monitor, 'poll_interval', None) else 60
    add_job(sync_engine.run_sync_task, "interval", minutes=interval_mins, id="auto_sync", replace_existing=True)

    if config.monitor.telegram.enabled:
        # 异步后台启动
        asyncio.create_task(telegram_monitor.start())
        
    yield
    # 关闭时执行
    logger.info("Shutting down Python-STRM application...")
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
from app.api import cloud115, cloud123, emby, strm, organize, web, system

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

# 注册各类路由
app.include_router(cloud115.router)
app.include_router(cloud123.router)
app.include_router(emby.router)
app.include_router(strm.router)
app.include_router(organize.router)
app.include_router(system.router, prefix="/api/v1")
app.include_router(web.router)

if __name__ == "__main__":
    import uvicorn
    config = get_config()
    uvicorn.run("app.main:app", host=config.server.host, port=config.server.port, reload=config.server.debug)
