from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

_scheduler = AsyncIOScheduler()

def get_scheduler() -> AsyncIOScheduler:
    """获取全局调度器"""
    return _scheduler

def start_scheduler():
    """启动调度器"""
    if not _scheduler.running:
        _scheduler.start()
        logger.info("Scheduler started")

def stop_scheduler():
    """停止调度器"""
    if _scheduler.running:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")

def add_job(func, trigger, **kwargs):
    """添加定时任务"""
    job = _scheduler.add_job(func, trigger, **kwargs)
    logger.info(f"Added job: {job.id}")
    return job

def remove_job(job_id: str):
    """移除定时任务"""
    _scheduler.remove_job(job_id)
    logger.info(f"Removed job: {job_id}")
