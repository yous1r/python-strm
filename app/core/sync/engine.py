import time
import asyncio
from loguru import logger
from datetime import datetime

from app.config import get_config
from app.database import get_db_conn
from app.core.cloud115.strm import generator_115
# from app.core.cloud123.strm import generator_123 # 如果 123 的生成器尚未实现，这里预留
from app.core.emby.manager import emby_manager
from app.core.emby.client import emby_client

class SyncEngine:
    async def run_sync_task(self):
        """执行单次完整同步任务"""
        config = get_config()
        start_time = time.time()
        logger.info("Starting automated STRM sync & scrape pipeline...")
        
        total_generated = 0
        details = []

        try:
            # 1. 扫描 115 网盘
            if config.cloud115.enabled and config.cloud115.cookie:
                logger.info("Scanning 115 cloud drive...")
                # 默认从根目录 '0' 开始扫描
                generated_115 = await generator_115.batch_generate(
                    dir_id='0',
                    output_dir=config.strm.output_dir,
                    base_url=config.strm.base_url,
                    recursive=True
                )
                count = len(generated_115)
                total_generated += count
                details.append(f"115: generated {count} files")
                
            # 2. 扫描 123 网盘 (预留扩展)
            if config.cloud123.enabled and config.cloud123.access_token:
                logger.info("Scanning 123 cloud drive... (Placeholder)")
                # generated_123 = await generator_123.batch_generate(0, config.strm.output_dir, config.strm.base_url)
                # count = len(generated_123)
                # total_generated += count
                # details.append(f"123: generated {count} files")

            # 3. 唤醒 Emby 刷新
            instances = await emby_manager.get_instances()
            for inst in instances:
                logger.info(f"Triggering Emby refresh for instance: {inst['name']}")
                await emby_client.refresh_library(inst['id'])

            duration = time.time() - start_time
            await self._record_history("AutoSync", "success", duration, total_generated, "; ".join(details))
            logger.info(f"Sync pipeline completed in {duration:.2f}s, generated {total_generated} STRM files.")

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Sync pipeline failed: {e}")
            await self._record_history("AutoSync", "failed", duration, total_generated, str(e))

    async def _record_history(self, task_name: str, status: str, duration: float, count: int, error_details: str):
        """将同步结果存入数据库，供前端历史追踪使用"""
        try:
            async with get_db_conn() as db:
                await db.execute('''
                    INSERT INTO sync_history (task_name, status, duration, processed_count, error_details)
                    VALUES (?, ?, ?, ?, ?)
                ''', (task_name, status, duration, count, error_details))
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to record sync history: {e}")

sync_engine = SyncEngine()
