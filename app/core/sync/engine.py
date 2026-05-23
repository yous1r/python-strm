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
                if not config.cloud115.sync_dirs:
                    logger.warning("No sync directories configured for 115. Skipping.")
                
                for sync_dir in config.cloud115.sync_dirs:
                    logger.info(f"Scanning 115 directory: {sync_dir.name} ({sync_dir.dir_id})")
                    # 将该目录名作为隔离输出的子文件夹
                    import os
                    target_out = os.path.join(config.strm.output_dir, sync_dir.name)
                    
                    generated_115 = await generator_115.batch_generate(
                        dir_id=sync_dir.dir_id,
                        output_dir=target_out,
                        base_url=config.strm.base_url,
                        recursive=True,
                        root_output_dir=config.strm.output_dir
                    )
                    count = len(generated_115)
                    total_generated += count
                    details.append(f"115 [{sync_dir.name}]: {count} files")
                
            # 2. 扫描 123 网盘 (预留扩展)
            if config.cloud123.enabled and config.cloud123.access_token:
                logger.info("Scanning 123 cloud drive... (Placeholder)")
                if config.cloud123.sync_dirs:
                    details.append("123: Placeholder implementation")

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
