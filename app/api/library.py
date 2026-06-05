import json
from typing import List
import asyncio

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form
from pydantic import BaseModel
from loguru import logger

from app.database import get_db_conn, insert_tg_resource
from app.core.monitor.telegram import telegram_monitor



router = APIRouter(prefix="/library", tags=["Resource Library"])

@router.get("/tg_resources")
async def get_tg_resources(page: int = 1, page_size: int = 20, search: str = ""):
    offset = (page - 1) * page_size
    params = []
    
    # 获取唯一的 base_title 列表（带分页）
    base_query = "SELECT base_title, MAX(poster_url) as poster_url, MAX(overview) as overview, MAX(cast_text) as cast_text, COUNT(*) as ep_count, MAX(msg_date) as last_updated FROM tg_resources"
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
        
        # 针对每个 group 拉取对应的所有剧集
        for group in groups:
            b_title = group['base_title']
            cursor = await db.execute("SELECT * FROM tg_resources WHERE base_title = ? ORDER BY msg_date DESC", (b_title,))
            group['episodes'] = await cursor.fetchall()
            
    return {"status": "success", "data": groups, "total": total, "page": page, "page_size": page_size}

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

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
                    
                    links = telegram_monitor.extract_links(raw_text)
                    if not links:
                        continue
                    
                    title = extract_title_from_text(raw_text)
                    msg_date = msg.get('date')
                    msg_id = msg.get('id')
                
                    guessed = guessit(title)
                    base_title = guessed.get("title") or title
                    poster_url = None
                
                    for link_data in links:
                        resource = {
                            "message_id": msg_id,
                            "channel_id": ch_id,
                            "title": title,
                            "raw_text": raw_text,
                            "link": link_data["url"],
                            "password": link_data["password"],
                            "disk_type": link_data["type"],
                            "msg_date": msg_date,
                            "status": "pending",
                            "base_title": base_title,
                            "poster_url": poster_url
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
            
    background_tasks.add_task(process_json_background, messages, channel_name, channel_id)
    return {"status": "success", "message": f"成功接收到 {len(messages)} 条历史消息，正在后台清洗解析并入库。"}

@router.post("/transfer/{res_id}")
async def manual_transfer(res_id: int):
    from app.events import event_bus, EVENT_MONITOR_NEW_LINK
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute("SELECT * FROM tg_resources WHERE id = ?", (res_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "资源不存在")
            
        await db.execute("UPDATE tg_resources SET status = 'queued' WHERE id = ?", (res_id,))
        await db.commit()
        
    link_data = {
        "url": row["link"],
        "password": row["password"],
        "type": row["disk_type"],
        "db_id": res_id,  # 传入 db_id 以便转存成功后更新状态
        "ignore_filters": True
    }
    await event_bus.emit(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')
    return {"status": "success"}

@router.post("/transfer_batch")
async def transfer_batch(base_title: str = Form(...)):
    from app.events import event_bus, EVENT_MONITOR_NEW_LINK
    
    async with get_db_conn() as db:
        db.row_factory = dict_factory
        cursor = await db.execute("SELECT * FROM tg_resources WHERE base_title = ? AND status IN ('pending', 'failed')", (base_title,))
        rows = await cursor.fetchall()
        
        if not rows:
            return {"status": "success", "message": "没有需要转存的剧集"}
            
        # 先批量更新状态
        ids = [r['id'] for r in rows]
        placeholders = ','.join('?' * len(ids))
        await db.execute(f"UPDATE tg_resources SET status = 'queued' WHERE id IN ({placeholders})", ids)
        await db.commit()
        
    for row in rows:
        link_data = {
            "url": row["link"],
            "password": row["password"],
            "type": row["disk_type"],
            "db_id": row["id"],
            "ignore_filters": True
        }
        await event_bus.emit(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')
        
    return {"status": "success", "message": f"已将 {len(rows)} 个资源加入转存队列"}

class TransferSelectedRequest(BaseModel):
    ids: List[int]
    base_title: str = ""
    transfer_all: bool = False

@router.post("/transfer_selected")
async def transfer_selected(req: TransferSelectedRequest):
    """批量转存选中的剧集，创建统一 task_id 追踪"""
    import uuid
    from app.events import event_bus, EVENT_MONITOR_NEW_LINK

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
        
        if not rows:
            return {"status": "success", "message": "所选资源均已转存，无需重复操作"}

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
    
    # 预解析第一个分享链接 → TMDB分类 → 创建归档目录结构
    series_folder_id = ""
    series_path_str = ""
    if req.base_title and len(rows) > 0:
        try:
            from app.core.transfer.classifier import classify, build_archive_path
            from app.core.cloud115.client import client_115
            from app.config import get_config
            
            # 预解析第一个分享链接，获取真实文件名
            first_row = rows[0]
            first_url = first_row["link"]
            first_code = first_row.get("password", "")
            
            # 提取 share_code 并调用 share_snap（仅预解析，不转存）
            share_code = ""
            if "s/" in first_url:
                share_code = first_url.split("s/")[1].split("?")[0]
            elif "share_code=" in first_url:
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(first_url)
                qs = parse_qs(parsed_url.query)
                if "share_code" in qs:
                    share_code = qs["share_code"][0]
            
            if share_code:
                api_type = get_config().cloud115.api_type
                snap_payload = {"share_code": share_code, "receive_code": first_code}
                if hasattr(client_115.client, "share_snap_app") and api_type != "web":
                    import asyncio as _asyncio
                    snap_info = await _asyncio.to_thread(client_115.client.share_snap_app, snap_payload)
                else:
                    import asyncio as _asyncio
                    snap_info = await _asyncio.to_thread(client_115.client.share_snap, snap_payload)
                
                if snap_info.get("state"):
                    share_list = snap_info.get("data", {}).get("list", [])
                    if share_list:
                        real_name = share_list[0].get("fn") or share_list[0].get("n") or ""
                        if real_name:
                            sample_name = real_name  # 用真实文件名替代DB标题
            else:
                sample_name = rows[0].get("title", req.base_title)
            
            # TMDB 分类
            cr = await classify(sample_name)
            if cr:
                path_parts = build_archive_path(cr)
                series_path_str = "/".join(path_parts)
                archive_id = get_config().transfer.archive_dir_id
                if archive_id and archive_id != "0":
                    res = await client_115.create_path(archive_id, series_path_str)
                    if "id" in res and res["id"]:
                        series_folder_id = res["id"]
                        logger.info(f"[Batch] 预解析+分类完成: {series_path_str} (cid={series_folder_id})")
                else:
                    logger.warning("[Batch] archive_dir_id 未配置，跳过目录创建")
        except Exception as e:
            logger.error(f"[Batch] 预解析/创建路径失败: {e}")
            sample_name = rows[0].get("title", req.base_title)
            try:
                cr = await classify(sample_name)
                if cr:
                    path_parts = build_archive_path(cr)
                    series_path_str = "/".join(path_parts)
                    archive_id = get_config().transfer.archive_dir_id
                    if archive_id and archive_id != "0":
                        res = await client_115.create_path(archive_id, series_path_str)
                        if "id" in res and res["id"]:
                            series_folder_id = res["id"]
            except:
                pass
    
    # emit 转存事件（带批次信息和间隔）
    for i, row in enumerate(rows):
        if i > 0:
            await asyncio.sleep(0.1)
        link_data = {
            "url": row["link"],
            "password": row["password"],
            "type": row["disk_type"],
            "db_id": row["id"],
            "ignore_filters": True,
            "batch_task_id": task_id,
            "episode_count": len(rows),
            "series_folder_id": series_folder_id,
            "series_path_str": series_path_str
        }
        await event_bus.emit(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')
    
    return {
        "status": "success",
        "task_id": task_id,
        "message": f"已将 {len(rows)} 集 ({req.base_title}) 加入转存队列"
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
    background_tasks.add_task(run_migration)
    return {"status": "success", "message": "后台清洗升级任务已启动"}
