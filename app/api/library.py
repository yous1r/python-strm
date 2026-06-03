from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from app.database import get_db_conn, insert_tg_resource
from app.core.monitor.telegram import telegram_monitor
import json

router = APIRouter(prefix="/library", tags=["Resource Library"])

@router.get("/tg_resources")
async def get_tg_resources(page: int = 1, page_size: int = 20, search: str = ""):
    offset = (page - 1) * page_size
    query = "SELECT * FROM tg_resources"
    params = []
    if search:
        query += " WHERE title LIKE ? OR raw_text LIKE ?"
        params.extend([f"%{search}%", f"%{search}%"])
        
    query += " ORDER BY msg_date DESC LIMIT ? OFFSET ?"
    params.extend([page_size, offset])
    
    count_query = "SELECT COUNT(*) FROM tg_resources"
    count_params = []
    if search:
        count_query += " WHERE title LIKE ? OR raw_text LIKE ?"
        count_params.extend([f"%{search}%", f"%{search}%"])
        
    async with get_db_conn() as db:
        cursor = await db.execute(count_query, count_params)
        total_row = await cursor.fetchone()
        total = total_row[0] if total_row else 0
        
        db.row_factory = dict_factory
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        
    return {"status": "success", "data": rows, "total": total, "page": page, "page_size": page_size}

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
        from app.core.monitor.parser import extract_title_from_text
        import asyncio
        async with get_db_conn() as db:
            for msg in msgs:
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
                        "status": "pending"
                    }
                    await insert_tg_resource(db, resource)
                # 每处理100条缓一下，防止锁死DB
                await asyncio.sleep(0)
            await db.commit()
            
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
        "db_id": res_id  # 传入 db_id 以便转存成功后更新状态
    }
    await event_bus.emit(EVENT_MONITOR_NEW_LINK, link_data=link_data, source='telegram')
    return {"status": "success"}
