"""转存整理管道 REST API"""
import uuid
import asyncio
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from loguru import logger

from app.events import event_bus, EVENT_TRANSFER_RECEIVED, EVENT_ROLLBACK_START
from app.core.cloud115.client import client_115
from app.core.transfer.models import TransferRequest
from app.database import get_db_conn
from app.config import get_config

router = APIRouter(prefix="/api/v1/transfer", tags=["转存整理"])


class ReceiveRequest(BaseModel):
    share_url: str
    receive_code: str = ""
    target_dir_id: str = ""
    filter_rules: Optional[List[str]] = None


@router.post("/receive")
async def receive_share(req: ReceiveRequest):
    """接收 115 分享链接，后台执行转存+移动+整理"""
    config = get_config()
    transfer_cfg = config.transfer

    if not transfer_cfg.enabled:
        raise HTTPException(status_code=400, detail="转存整理管道未启用")

    # 确定目标临时目录
    target_dir = req.target_dir_id or transfer_cfg.temp_dir_id
    if not target_dir:
        raise HTTPException(status_code=400, detail="未配置临时目录 (temp_dir_id)")

    # 转存（文件落入 115 默认收件箱）
    res = await client_115.share_receive(
        req.share_url,
        req.receive_code,
        target_dir_id="0",  # 115 接口忽略此参数，文件始终落入最近接收
        filter_rules=req.filter_rules
    )

    if not res.get("state"):
        raise HTTPException(status_code=400, detail=res.get("error", "转存失败"))

    # 获取收件箱中最新的文件（简化：直接列出 inbox_dir_id）
    inbox_id = transfer_cfg.inbox_dir_id
    files_res = await client_115.list_files(inbox_id, limit=50)
    files = []
    if not files_res.get("error"):
        for item in files_res.get("items", []):
            if not item.get("is_dir"):
                files.append({
                    "cid": item.get("cid") or item.get("fid"),
                    "name": item.get("n"),
                    "parent_cid": inbox_id
                })

    task_id = str(uuid.uuid4())

    # emit 事件，触发 mover → organizer 链
    asyncio.create_task(
        event_bus.emit(
            EVENT_TRANSFER_RECEIVED,
            share_url=req.share_url,
            inbox_dir_id=inbox_id,
            temp_dir_id=target_dir,
            files=files,
            task_id=task_id
        )
    )

    return {
        "status": "success",
        "task_id": task_id,
        "msg": f"转存已接收，共 {len(files)} 个文件，正在后台处理"
    }


@router.get("/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """列出转存整理任务"""
    async with get_db_conn() as db:
        if status:
            cursor = await db.execute(
                "SELECT * FROM transfer_tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM transfer_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return {"tasks": [dict(r) for r in rows]}


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: str):
    """获取任务详情，含操作日志"""
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM transfer_tasks WHERE task_id=?",
            (task_id,)
        )
        task = await cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        cursor = await db.execute(
            "SELECT * FROM operation_logs WHERE task_id=? ORDER BY seq ASC",
            (task_id,)
        )
        ops = await cursor.fetchall()

        result = dict(task)
        result["operations"] = [dict(o) for o in ops]
        return result


@router.post("/organize/run")
async def manual_organize(temp_dir_id: str):
    """手动触发整理指定的临时目录"""
    config = get_config()
    transfer_cfg = config.transfer
    archive_dir_id = transfer_cfg.archive_dir_id

    if not archive_dir_id:
        raise HTTPException(status_code=400, detail="未配置归档目录")

    files_res = await client_115.list_files(temp_dir_id, limit=200)
    files = []
    if not files_res.get("error"):
        for item in files_res.get("items", []):
            if not item.get("is_dir"):
                files.append({
                    "cid": item.get("cid") or item.get("fid"),
                    "name": item.get("n"),
                    "parent_cid": temp_dir_id
                })

    task_id = str(uuid.uuid4())

    asyncio.create_task(
        event_bus.emit(
            "transfer_moved",  # EVENT_TRANSFER_MOVED
            task_id=task_id,
            temp_dir_id=temp_dir_id,
            files=files
        )
    )

    return {
        "status": "success",
        "task_id": task_id,
        "msg": f"整理任务已启动，共 {len(files)} 个文件"
    }


@router.get("/organize/tasks")
async def list_organize_tasks(limit: int = 50):
    """列出整理任务"""
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM transfer_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return {"tasks": [dict(r) for r in rows]}


@router.get("/organize/tasks/{task_id}")
async def get_organize_task_detail(task_id: str):
    """获取整理任务详情"""
    return await get_task_detail(task_id)


@router.post("/rollback/{task_id}")
async def rollback_task(task_id: str):
    """一键还原指定任务的所有操作"""
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT status FROM transfer_tasks WHERE task_id=?",
            (task_id,)
        )
        task = await cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] == "rolled_back":
            raise HTTPException(status_code=400, detail="任务已还原")

    asyncio.create_task(
        event_bus.emit(EVENT_ROLLBACK_START, task_id=task_id)
    )

    return {
        "status": "success",
        "task_id": task_id,
        "msg": "还原任务已启动"
    }


@router.get("/rollback/{task_id}/preview")
async def preview_rollback(task_id: str):
    """预览还原操作（不执行）"""
    async with get_db_conn() as db:
        cursor = await db.execute(
            "SELECT * FROM operation_logs WHERE task_id=? AND status='done' ORDER BY seq DESC",
            (task_id,)
        )
        ops = await cursor.fetchall()

        return {
            "task_id": task_id,
            "total_ops": len(ops),
            "operations": [dict(o) for o in ops]
        }


@router.get("/categories")
async def get_categories():
    """返回当前分类配置"""
    config = get_config()
    categories = config.transfer.categories
    if not categories:
        categories = config.transfer.default_categories()
    return {
        "categories": [
            {"name": c.name, "subcategories": c.subcategories}
            for c in categories
        ]
    }