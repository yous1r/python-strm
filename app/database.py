import aiosqlite
import os
from contextlib import asynccontextmanager
from app.config import get_config
from loguru import logger

async def init_db():
    """初始化数据库表结构"""
    config = get_config()
    db_path = config.database.path
    
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
        
        # 同步自动化历史表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                status TEXT NOT NULL,
                duration REAL,
                processed_count INTEGER DEFAULT 0,
                error_details TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Telegram资源库表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tg_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                channel_id TEXT,
                title TEXT NOT NULL,
                raw_text TEXT,
                link TEXT NOT NULL UNIQUE,
                password TEXT,
                disk_type TEXT NOT NULL,
                msg_date DATETIME,
                status TEXT DEFAULT 'pending',
                base_title TEXT,
                poster_url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 兼容老表升级：尝试新增字段
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN base_title TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN poster_url TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN overview TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN cast_text TEXT")
        except Exception: pass
        
        await db.commit()
        logger.info("Database initialized successfully")

@asynccontextmanager
async def get_db_conn():
    """获取数据库连接上下文管理器"""
    config = get_config()
    conn = await aiosqlite.connect(config.database.path, timeout=15.0)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()

async def insert_tg_resource(db, resource: dict) -> bool:
    """插入资源，如果链接已存在则忽略。返回 True 表示新插入，False 表示已存在/忽略"""
    cursor = await db.execute('''
        INSERT OR IGNORE INTO tg_resources 
        (message_id, channel_id, title, raw_text, link, password, disk_type, msg_date, status, base_title, poster_url, overview, cast_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        resource.get('message_id'),
        resource.get('channel_id'),
        resource.get('title'),
        resource.get('raw_text'),
        resource.get('link'),
        resource.get('password'),
        resource.get('disk_type'),
        resource.get('msg_date'),
        resource.get('status', 'pending'),
        resource.get('base_title'),
        resource.get('poster_url'),
        resource.get('overview'),
        resource.get('cast_text')
    ))
    return cursor.rowcount > 0
