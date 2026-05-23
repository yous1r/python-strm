import aiosqlite
import os
from contextlib import asynccontextmanager
from app.config import get_config
from loguru import logger

async def init_db():
    """初始化数据库表结构"""
    config = get_config()
    db_path = config.database.path
    
    # 如果已有数据（数据库文件存在且有内容），则不再进行初始化
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        logger.info(f"Database {db_path} already exists, skipping initialization.")
        return
    
    # 确保目录存在
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    logger.info(f"Initializing database at {db_path}")
    
    async with get_db_conn() as db:
        # 云盘账号表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cloud_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                cookie TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 媒体项目表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS media_items (
                id TEXT PRIMARY KEY,
                cloud_type TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                size INTEGER,
                is_dir BOOLEAN,
                pickcode TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # STRM记录表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strm_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                cloud_type TEXT NOT NULL,
                strm_path TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(file_id, cloud_type)
            )
        ''')
        
        # 整理历史表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS organize_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                cloud_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Emby实例表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS emby_instances (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.commit()
        logger.info("Database initialized successfully")

@asynccontextmanager
async def get_db_conn():
    """获取数据库连接上下文管理器"""
    config = get_config()
    conn = await aiosqlite.connect(config.database.path)
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()
