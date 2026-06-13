import json
from typing import List
import asyncio

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form
from pydantic import BaseModel
from loguru import logger

from app.database import get_db_conn, insert_tg_resource
from app.core.monitor.telegram import telegram_monitor
from app.services.telegram_resource_transfer_service import normalize_resource_payload, process_resource_transfer
from app.utils.background_tasks import CLOUD_API_POOL, LOCAL_DB_POOL, spawn_background_task



router = APIRouter(prefix="/library", tags=["Resource Library"])

@router.get("/tg_resources")
async def get_tg_resources(page: int = 1, page_size: int = 20, search: str = ""):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size
    params = []
    
    # 首页只返回分组摘要，避免把每个资源分组下的所有剧集一次性查询并渲染到 DOM。
    base_query = "SELECT base_title, MAX(poster_url) as poster_url, MAX(overview) as overview, COUNT(*) as ep_count, MAX(msg_date) as last_updated FROM tg_resources"
    if search:
        base_query += " WHERE title LIKE ? OR raw_text LIKE ?"
        params.extend([f"%{search}%", f"%{search}%"])
    base_query += " GROUP BY base_title ORDER BY last_updated DESC LIMIT ? OFFSET ?"
    params.extend([page_size, offset])
    
    count_query = "SELECT COUNT(DISTINCT base_title) FROM tg_resources"
    count_params = []
    if search:
        count_query += " WHERE title LIKE ? OR raw_text LIKE ?"
        count_params.extend([f"%{search}%", f"%{search}%"])
        
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute(count_query, count_params)
        total_row = await cursor.fetchone()
        total = total_row['COUNT(DISTINCT base_title)'] if total_row else 0
        
        cursor = await db.execute(base_query, params)
        groups = await cursor.fetchall()
        
    has_more = page * page_size < total
    return {
        "status": "success",
        "data": groups,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }

@router.get("/tg_resources/episodes")
async def get_tg_resource_episodes(base_title: str):
    """按需加载某个资源分组下的全部剧集/资源条目。"""
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute(
            """
            SELECT base_title, MAX(poster_url) as poster_url, MAX(overview) as overview,
                   COUNT(*) as ep_count, MAX(msg_date) as last_updated
            FROM tg_resources
            WHERE base_title = ?
            GROUP BY base_title
            """,
            (base_title,)
        )
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(404, "资源不存在")

        cursor = await db.execute(
            "SELECT * FROM tg_resources WHERE base_title = ? ORDER BY msg_date DESC, id DESC",
            (base_title,)
        )
        episodes = await cursor.fetchall()

    return {"status": "success", "data": group, "episodes": episodes}

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def _normalize_transfer_rows(rows: list[dict]) -> tuple[list[dict], int]:
    normalized_rows = [normalize_resource_payload(dict(row)) for row in rows]
    transferable_rows = [row for row in normalized_rows if row.get("type") == "115" and row.get("url")]
    skipped_count = len(normalized_rows) - len(transferable_rows)
    return transferable_rows, skipped_count

@router.post("/upload_json")
async def upload_tg_json(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.endswith('.json'):
        raise HTTPException(400, "仅支持 JSON 文件")
        
    content = await file.read()
    try:
        data = json.loads(content)
    except:
        raise HTTPException(400, "JSON 文件格式错误")
        
    messages = data.get('messages', [])
    channel_name = data.get('name', '未知频道')
    channel_id = str(data.get('id', ''))
    
    async def process_json_background(msgs, ch_name, ch_id):
        from loguru import logger
        logger.info(f"Started process_json_background for {len(msgs)} msgs")
        try:
            from app.core.monitor.parser import extract_title_from_text
            import asyncio
            from guessit import guessit
            async with get_db_conn() as db:
                for i, msg in enumerate(msgs):
                    if msg.get('type') != 'message':
                        continue
                    # 解析消息文本
                    raw_text = ""
                    entities = msg.get('text_entities')
                    if not entities:
                        entities = msg.get('text', [])
                        if isinstance(entities, str):
                            entities = [{"type": "plain", "text": entities}]
                
                    if isinstance(entities, list):
                        for part in entities:
                            if isinstance(part, str):
                                raw_text += part
                            elif isinstance(part, dict):
                                raw_text += part.get('text', '')
                                # 把隐藏的超链接暴露在纯文本里，让后续的 extract_links 能正则捕获到
                                if part.get('href'):
                                    raw_text += f" {part['href']} "

                    resource_summary = telegram_monitor.summarize_resources(raw_text)
                    if not resource_summary:
                        continue
                    
                    title = extract_title_from_text(raw_text)
                    msg_date = msg.get('date')
                    msg_id = msg.get('id')
                
                    guessed = guessit(title)
                    base_title = guessed.get("title") or title
                    poster_url = None

                    resource = {
                        "message_id": msg_id,
                        "channel_id": ch_id,
                        "title": title,
                        "raw_text": raw_text,
                        "link": resource_summary["link"],
                        "password": resource_summary["password"],
                        "disk_type": resource_summary["disk_type"],
                        "msg_date": msg_date,
                        "status": "pending",
                        "base_title": base_title,
                        "poster_url": poster_url,
                        "resource_links": json.dumps(resource_summary["resource_links"], ensure_ascii=False),
                        "url_links": json.dumps(resource_summary["url_links"], ensure_ascii=False),
                        "magnet_links": json.dumps(resource_summary["magnet_links"], ensure_ascii=False),
                        "torrent_files": json.dumps(resource_summary["torrent_files"], ensure_ascii=False),
                        "resource_count": resource_summary["resource_count"],
                    }
                    await insert_tg_resource(db, resource)
                    
                    # 避免长事务锁表
                    if i > 0 and i % 200 == 0:
                        await db.commit()
                        await asyncio.sleep(0.1)
                await db.commit()
            logger.info("Finished process_json_background successfully")
        except Exception as e:
            logger.exception(f"Error in process_json_background: {e}")
            
    spawn_background_task(
        lambda: process_json_background(messages, channel_name, channel_id),
        name="telegram_json_import",
        pool=LOCAL_DB_POOL,
    )
    return {"status": "success", "message": f"成功接收到 {len(messages)} 条历史消息，正在后台清洗解析并入库。"}

@router.post("/transfer/{res_id}")
async def manual_transfer(res_id: int):
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute("SELECT * FROM tg_resources WHERE id = ?", (res_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "资源不存在")

        normalized_row = normalize_resource_payload(dict(row))
        if normalized_row.get("type") != "115" or not normalized_row.get("url"):
            raise HTTPException(400, "当前资源缺少可转存的 115 网盘链接")
            
        await db.execute("UPDATE tg_resources SET status = 'queued' WHERE id = ?", (res_id,))
        await db.commit()
        
    link_data = {
        "url": normalized_row["url"],
        "password": normalized_row["password"],
        "type": normalized_row["type"],
        "db_id": res_id,  # 传入 db_id 以便转存成功后更新状态
        "ignore_filters": True
    }
    spawn_background_task(
        lambda: process_resource_transfer(link_data, source="telegram"),
        name="telegram_resource_transfer",
        pool=CLOUD_API_POOL,
    )
    return {"status": "success"}

@router.post("/transfer_batch")
async def transfer_batch(base_title: str = Form(...)):
    import uuid
    from app.events import event_bus, EVENT_TRANSFER_BATCH_REQUESTED
    
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute("SELECT * FROM tg_resources WHERE base_title = ? AND status IN ('pending', 'failed')", (base_title,))
        rows = await cursor.fetchall()
        rows, skipped_count = _normalize_transfer_rows(rows)
        
        if not rows:
            message = "没有可转存的 115 网盘剧集"
            if skipped_count:
                message += f"，已跳过 {skipped_count} 条无效或非 115 链接"
            return {"status": "success", "message": message}
            
        # 先批量更新状态
        ids = [r['id'] for r in rows]
        placeholders = ','.join('?' * len(ids))
        await db.execute(f"UPDATE tg_resources SET status = 'queued' WHERE id IN ({placeholders})", ids)
        await db.commit()
        
    task_id = str(uuid.uuid4())

    async with get_db_conn() as db:
        await db.execute(
            """INSERT OR REPLACE INTO transfer_tasks
               (task_id, status, source_dir_id, archive_dir_id, file_count)
               VALUES (?, 'pending', 'library_batch', 'library_batch', ?)""",
            (task_id, len(rows))
        )
        await db.commit()

    event_bus.emit_background(
        EVENT_TRANSFER_BATCH_REQUESTED,
        task_id=task_id,
        rows=rows,
        base_title=base_title,
        name=f"batch_transfer:{task_id}",
    )
        
    message = f"已将 {len(rows)} 个资源加入转存队列"
    if skipped_count:
        message += f"，已跳过 {skipped_count} 条无效或非 115 链接"
    return {"status": "success", "task_id": task_id, "message": message}

class TransferSelectedRequest(BaseModel):
    ids: List[int]
    base_title: str = ""
    transfer_all: bool = False

@router.post("/transfer_selected")
async def transfer_selected(req: TransferSelectedRequest):
    """批量转存选中的剧集，创建统一 task_id 追踪"""
    import uuid
    from app.events import event_bus, EVENT_TRANSFER_BATCH_REQUESTED

    task_id = str(uuid.uuid4())

    async with get_db_conn() as db:
        db.row_factory = dict_factory

        if req.transfer_all and req.base_title:
            # 整剧转存：查该 base_title 下所有 pending/failed 的剧集
            cursor = await db.execute(
                "SELECT * FROM tg_resources WHERE base_title = ? AND status IN ('pending', 'failed')",
                (req.base_title,)
            )
            rows = await cursor.fetchall()
        elif req.ids:
            placeholders = ','.join('?' * len(req.ids))
            cursor = await db.execute(
                f"SELECT * FROM tg_resources WHERE id IN ({placeholders}) AND status IN ('pending', 'failed')",
                req.ids
            )
            rows = await cursor.fetchall()
        else:
            return {"status": "error", "message": "未指定资源"}

        rows, skipped_count = _normalize_transfer_rows(rows)
        
        if not rows:
            message = "所选资源中没有可转存的 115 网盘链接"
            if skipped_count:
                message += f"，已跳过 {skipped_count} 条无效或非 115 链接"
            return {"status": "success", "message": message}

        # 更新状态
        actual_ids = [r['id'] for r in rows]
        id_placeholders = ','.join('?' * len(actual_ids))
        await db.execute(
            f"UPDATE tg_resources SET status = 'queued' WHERE id IN ({id_placeholders})",
            actual_ids
        )
        await db.commit()
    
    # 创建批次任务记录
    async with get_db_conn() as db:
        await db.execute(
            """INSERT OR REPLACE INTO transfer_tasks
               (task_id, status, source_dir_id, archive_dir_id, file_count)
               VALUES (?, 'pending', 'library_batch', 'library_batch', ?)""",
            (task_id, len(rows))
        )
        await db.commit()
    
    event_bus.emit_background(
        EVENT_TRANSFER_BATCH_REQUESTED,
        task_id=task_id,
        rows=rows,
        base_title=req.base_title,
        name=f"batch_transfer:{task_id}",
    )
    
    return {
        "status": "success",
        "task_id": task_id,
        "message": (
            f"已将 {len(rows)} 集 ({req.base_title}) 加入转存队列"
            + (f"，已跳过 {skipped_count} 条无效或非 115 链接" if skipped_count else "")
        )
    }

@router.post("/migrate_legacy")
async def migrate_legacy(background_tasks: BackgroundTasks):
    async def run_migration():
        from guessit import guessit
        from app.core.tmdb.client import tmdb_client
        import asyncio
        async with get_db_conn() as db:
            db.row_factory = dict_factory
            cursor = await db.execute("SELECT id, title FROM tg_resources WHERE poster_url IS NULL ORDER BY msg_date DESC")
            rows = await cursor.fetchall()
            cache = {}
            import httpx
            async with httpx.AsyncClient() as client:
                for row in rows:
                    guessed = guessit(row['title'])
                    b_title = guessed.get("title") or row['title']
                    year = str(guessed.get("year", ""))
                    
                    poster, overview, cast_text = None, None, None
                    cache_key = f"{b_title}_{year}"
                    
                    if cache_key in cache:
                        poster, overview, cast_text = cache[cache_key]
                    else:
                        try:
                            res = await tmdb_client.search_movie(b_title, year)
                            item_type = 'movie'
                            if not res:
                                res = await tmdb_client.search_tv(b_title, year)
                                item_type = 'tv'
                                
                            if res:
                                item = res[0]
                                if item.get('poster_path'):
                                    poster = f"https://image.tmdb.org/t/p/w342{item['poster_path']}"
                                overview = item.get('overview', '')
                                tmdb_id = item.get('id')
                                
                                # fetch credits
                                if tmdb_id:
                                    credits_url = f"https://api.themoviedb.org/3/{item_type}/{tmdb_id}/credits?api_key={tmdb_client.config.api_key}&language={tmdb_client.config.language}"
                                    c_res = await client.get(credits_url, proxies={"http://": tmdb_client.config.proxy, "https://": tmdb_client.config.proxy} if tmdb_client.config.proxy else None)
                                    if c_res.status_code == 200:
                                        cast_data = c_res.json().get('cast', [])[:5]
                                        cast_text = ", ".join([c['name'] for c in cast_data])
                        except Exception as e:
                            print(e)
                        cache[cache_key] = (poster, overview, cast_text)
                        await asyncio.sleep(0.5)
                        
                    await db.execute("UPDATE tg_resources SET base_title=?, poster_url=?, overview=?, cast_text=? WHERE id=?", 
                                     (b_title, poster, overview, cast_text, row['id']))
                    await db.commit()
    spawn_background_task(
        run_migration,
        name="telegram_legacy_migration",
        pool=LOCAL_DB_POOL,
    )
    return {"status": "success", "message": "后台清洗升级任务已启动"}
