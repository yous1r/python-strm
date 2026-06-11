import aiosqlite
import os
from contextlib import asynccontextmanager
from datetime import datetime
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
                cloud_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                source_file_name TEXT,
                source_pickcode TEXT,
                source_sha TEXT,
                source_archive_dir_id TEXT,
                archive_dir_id TEXT,
                archive_rel_path TEXT,
                strm_rel_path TEXT,
                strm_abs_path TEXT,
                strm_path TEXT,
                play_identity TEXT,
                status TEXT DEFAULT 'generated',
                task_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cloud_type, file_id)
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_strm_records_play_identity
            ON strm_records(cloud_type, play_identity)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_strm_records_paths
            ON strm_records(strm_abs_path, strm_path, strm_rel_path)
        ''')

        # 媒体服务器扩展索引表：将 Emby/Jellyfin/FNOS item 映射到 STRM 核心播放身份
        await db.execute('''
            CREATE TABLE IF NOT EXISTS media_item_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_server_type TEXT NOT NULL,
                media_server_name TEXT NOT NULL DEFAULT '',
                media_item_id TEXT NOT NULL,
                media_source_id TEXT NOT NULL DEFAULT '',
                cloud_type TEXT NOT NULL,
                file_id TEXT,
                play_identity TEXT NOT NULL,
                strm_record_id INTEGER,
                source_path TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(media_server_type, media_server_name, media_item_id, media_source_id),
                FOREIGN KEY(strm_record_id) REFERENCES strm_records(id)
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_media_item_links_cloud_file
            ON media_item_links(cloud_type, file_id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_media_item_links_play_identity
            ON media_item_links(cloud_type, play_identity)
        ''')
        
        # 转存整理任务表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transfer_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                source_dir_id TEXT NOT NULL,
                archive_dir_id TEXT NOT NULL,
                file_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                error_detail TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        ''')
        
        # 操作日志表（可追溯、可还原）
        await db.execute('''
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                file_cid TEXT NOT NULL,
                file_name TEXT NOT NULL,
                new_name TEXT,
                source_cid TEXT NOT NULL,
                target_cid TEXT NOT NULL,
                op_type TEXT NOT NULL,
                status TEXT DEFAULT 'done',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES transfer_tasks(task_id)
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
                link TEXT NOT NULL,
                password TEXT,
                disk_type TEXT NOT NULL,
                msg_date DATETIME,
                status TEXT DEFAULT 'pending',
                base_title TEXT,
                poster_url TEXT,
                overview TEXT,
                cast_text TEXT,
                resource_links TEXT,
                url_links TEXT,
                magnet_links TEXT,
                torrent_files TEXT,
                resource_count INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await _migrate_tg_resources_unique_constraint(db)
        await db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tg_resources_channel_message
            ON tg_resources(channel_id, message_id)
        ''')
        
        # Telegram 频道监听状态表
        await db.execute('''
            CREATE TABLE IF NOT EXISTS telegram_monitor_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_ref TEXT NOT NULL UNIQUE,
                resolved_channel_id TEXT,
                last_message_id INTEGER,
                last_message_date TEXT,
                last_success_at TEXT,
                last_error TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS telegram_history_sync_checkpoint (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_key TEXT NOT NULL UNIQUE,
                channel_ref TEXT NOT NULL,
                range_start TEXT NOT NULL,
                range_end TEXT NOT NULL,
                status TEXT NOT NULL,
                last_message_id INTEGER,
                last_message_date TEXT,
                processed_count INTEGER DEFAULT 0,
                inserted_count INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_telegram_history_sync_checkpoint_channel_range
            ON telegram_history_sync_checkpoint(channel_ref, range_start, range_end)
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
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN resource_links TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN url_links TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN magnet_links TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN torrent_files TEXT")
        except Exception: pass
        try:
            await db.execute("ALTER TABLE tg_resources ADD COLUMN resource_count INTEGER DEFAULT 1")
        except Exception: pass

        # 兼容旧版 STRM 记录表结构，按缺列逐步补齐
        async with db.execute("PRAGMA table_info(strm_records)") as cursor:
            strm_columns = {row[1] for row in await cursor.fetchall()}

        strm_record_columns = {
            "source_file_name": "ALTER TABLE strm_records ADD COLUMN source_file_name TEXT",
            "source_pickcode": "ALTER TABLE strm_records ADD COLUMN source_pickcode TEXT",
            "source_sha": "ALTER TABLE strm_records ADD COLUMN source_sha TEXT",
            "source_archive_dir_id": "ALTER TABLE strm_records ADD COLUMN source_archive_dir_id TEXT",
            "archive_dir_id": "ALTER TABLE strm_records ADD COLUMN archive_dir_id TEXT",
            "archive_rel_path": "ALTER TABLE strm_records ADD COLUMN archive_rel_path TEXT",
            "strm_rel_path": "ALTER TABLE strm_records ADD COLUMN strm_rel_path TEXT",
            "strm_abs_path": "ALTER TABLE strm_records ADD COLUMN strm_abs_path TEXT",
            "strm_path": "ALTER TABLE strm_records ADD COLUMN strm_path TEXT",
            "play_identity": "ALTER TABLE strm_records ADD COLUMN play_identity TEXT",
            "status": "ALTER TABLE strm_records ADD COLUMN status TEXT DEFAULT 'generated'",
            "task_id": "ALTER TABLE strm_records ADD COLUMN task_id TEXT",
            "updated_at": "ALTER TABLE strm_records ADD COLUMN updated_at DATETIME",
        }
        for column_name, ddl in strm_record_columns.items():
            if column_name not in strm_columns:
                await db.execute(ddl)

        async with db.execute("PRAGMA table_info(media_item_links)") as cursor:
            media_link_columns = {row[1] for row in await cursor.fetchall()}

        media_item_link_columns = {
            "media_source_id": "ALTER TABLE media_item_links ADD COLUMN media_source_id TEXT NOT NULL DEFAULT ''",
            "strm_record_id": "ALTER TABLE media_item_links ADD COLUMN strm_record_id INTEGER",
            "source_path": "ALTER TABLE media_item_links ADD COLUMN source_path TEXT",
            "updated_at": "ALTER TABLE media_item_links ADD COLUMN updated_at DATETIME",
        }
        for column_name, ddl in media_item_link_columns.items():
            if column_name not in media_link_columns:
                await db.execute(ddl)

        await db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_media_item_links_item
            ON media_item_links(media_server_type, media_server_name, media_item_id, media_source_id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_media_item_links_cloud_file
            ON media_item_links(cloud_type, file_id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_media_item_links_play_identity
            ON media_item_links(cloud_type, play_identity)
        ''')
        
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

async def insert_tg_resource(db, resource: dict) -> dict | None:
    """插入资源，如果同频道同消息已存在则忽略并返回 None。"""
    cursor = await db.execute('''
        INSERT OR IGNORE INTO tg_resources 
        (
            message_id, channel_id, title, raw_text, link, password, disk_type,
            msg_date, status, base_title, poster_url, overview, cast_text,
            resource_links, url_links, magnet_links, torrent_files, resource_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        resource.get('cast_text'),
        resource.get('resource_links'),
        resource.get('url_links'),
        resource.get('magnet_links'),
        resource.get('torrent_files'),
        resource.get('resource_count', 1),
    ))
    if cursor.rowcount <= 0:
        return None

    async with db.execute(
        '''
        SELECT *
        FROM tg_resources
        WHERE id = ?
        ''',
        (cursor.lastrowid,),
    ) as row_cursor:
        row = await row_cursor.fetchone()
        return dict(row) if row else None


async def _migrate_tg_resources_unique_constraint(db) -> None:
    """统一 tg_resources 唯一约束为同频道同消息唯一。"""
    async with db.execute("PRAGMA index_list(tg_resources)") as cursor:
        indexes = await cursor.fetchall()

    unique_index_names = [row[1] for row in indexes if row[2]]
    needs_migration = False
    for index_name in unique_index_names:
        async with db.execute(f"PRAGMA index_info({index_name})") as cursor:
            columns = [row[2] for row in await cursor.fetchall()]
        if columns in (["link"], ["channel_id", "message_id", "link"]):
            needs_migration = True
            break

    if not needs_migration:
        return

    logger.info("Migrating tg_resources unique constraint to channel/message level")
    await db.execute("ALTER TABLE tg_resources RENAME TO tg_resources_legacy")
    await db.execute('''
        CREATE TABLE tg_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            channel_id TEXT,
            title TEXT NOT NULL,
            raw_text TEXT,
            link TEXT NOT NULL,
            password TEXT,
            disk_type TEXT NOT NULL,
            msg_date DATETIME,
            status TEXT DEFAULT 'pending',
            base_title TEXT,
            poster_url TEXT,
            overview TEXT,
            cast_text TEXT,
            resource_links TEXT,
            url_links TEXT,
            magnet_links TEXT,
            torrent_files TEXT,
            resource_count INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        INSERT INTO tg_resources (
            message_id, channel_id, title, raw_text, link, password, disk_type,
            msg_date, status, base_title, poster_url, overview, cast_text,
            resource_links, url_links, magnet_links, torrent_files, resource_count, created_at
        )
        SELECT
            message_id, channel_id, title, raw_text, link, password, disk_type,
            msg_date, status, base_title, poster_url, overview, cast_text,
            NULL, NULL, NULL, NULL, 1, MAX(created_at)
        FROM tg_resources_legacy
        GROUP BY channel_id, message_id
    ''')
    await db.execute("DROP TABLE tg_resources_legacy")
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tg_resources_channel_message
        ON tg_resources(channel_id, message_id)
    ''')


async def upsert_telegram_monitor_state(
    channel_ref: str,
    resolved_channel_id: str | None = None,
    last_message_id: int | None = None,
    last_message_date: str | None = None,
    last_error: str = "",
) -> None:
    now = datetime.utcnow().isoformat()
    async with get_db_conn() as db:
        await db.execute(
            '''
            INSERT INTO telegram_monitor_state (
                channel_ref, resolved_channel_id, last_message_id, last_message_date,
                last_success_at, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_ref) DO UPDATE SET
                resolved_channel_id=excluded.resolved_channel_id,
                last_message_id=COALESCE(excluded.last_message_id, telegram_monitor_state.last_message_id),
                last_message_date=COALESCE(excluded.last_message_date, telegram_monitor_state.last_message_date),
                last_success_at=excluded.last_success_at,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            ''',
            (
                channel_ref,
                resolved_channel_id,
                last_message_id,
                last_message_date,
                now,
                last_error,
                now,
            ),
        )
        await db.commit()


async def get_telegram_monitor_state(channel_ref: str):
    async with get_db_conn() as db:
        async with db.execute(
            '''
            SELECT channel_ref, resolved_channel_id, last_message_id, last_message_date,
                   last_success_at, last_error, updated_at
            FROM telegram_monitor_state
            WHERE channel_ref = ?
            ''',
            (channel_ref,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_telegram_monitor_states() -> list[dict]:
    async with get_db_conn() as db:
        async with db.execute(
            '''
            SELECT channel_ref, resolved_channel_id, last_message_id, last_message_date,
                   last_success_at, last_error, updated_at
            FROM telegram_monitor_state
            ORDER BY channel_ref ASC
            '''
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def upsert_telegram_history_sync_checkpoint(
    checkpoint_key: str,
    *,
    channel_ref: str,
    range_start: str,
    range_end: str,
    status: str,
    last_message_id: int | None = None,
    last_message_date: str | None = None,
    processed_count: int = 0,
    inserted_count: int = 0,
    last_error: str = "",
) -> None:
    now = datetime.utcnow().isoformat()
    async with get_db_conn() as db:
        await db.execute(
            '''
            INSERT INTO telegram_history_sync_checkpoint (
                checkpoint_key, channel_ref, range_start, range_end, status,
                last_message_id, last_message_date, processed_count, inserted_count,
                last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checkpoint_key) DO UPDATE SET
                status=excluded.status,
                last_message_id=excluded.last_message_id,
                last_message_date=excluded.last_message_date,
                processed_count=excluded.processed_count,
                inserted_count=excluded.inserted_count,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            ''',
            (
                checkpoint_key,
                channel_ref,
                range_start,
                range_end,
                status,
                last_message_id,
                last_message_date,
                processed_count,
                inserted_count,
                last_error,
                now,
            ),
        )
        await db.commit()


async def get_telegram_history_sync_checkpoint(checkpoint_key: str):
    async with get_db_conn() as db:
        async with db.execute(
            '''
            SELECT checkpoint_key, channel_ref, range_start, range_end, status,
                   last_message_id, last_message_date, processed_count, inserted_count,
                   last_error, updated_at
            FROM telegram_history_sync_checkpoint
            WHERE checkpoint_key = ?
            ''',
            (checkpoint_key,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_telegram_history_sync_checkpoints(channel_ref: str | None = None) -> list[dict]:
    async with get_db_conn() as db:
        if channel_ref:
            query = '''
                SELECT checkpoint_key, channel_ref, range_start, range_end, status,
                       last_message_id, last_message_date, processed_count, inserted_count,
                       last_error, updated_at
                FROM telegram_history_sync_checkpoint
                WHERE channel_ref = ?
                ORDER BY range_start ASC, range_end ASC
            '''
            params = (channel_ref,)
        else:
            query = '''
                SELECT checkpoint_key, channel_ref, range_start, range_end, status,
                       last_message_id, last_message_date, processed_count, inserted_count,
                       last_error, updated_at
                FROM telegram_history_sync_checkpoint
                ORDER BY channel_ref ASC, range_start ASC, range_end ASC
            '''
            params = ()

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
