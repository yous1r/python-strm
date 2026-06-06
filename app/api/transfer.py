"""转存整理管道 REST API"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.transfer_service import (
    TransferServiceError,
    get_transfer_categories,
    get_transfer_task_detail,
    list_transfer_tasks,
    preview_rollback_task,
    receive_share_task,
    run_manual_organize_task,
    start_rollback_task,
)

router = APIRouter(prefix="/api/v1/transfer", tags=["转存整理"])


class ReceiveRequest(BaseModel):
    share_url: str
    receive_code: str = ""
    target_dir_id: str = ""
    filter_rules: Optional[List[str]] = None


def _raise_transfer_http_error(exc: TransferServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/receive")
async def receive_share(req: ReceiveRequest):
    """接收 115 分享链接，后台执行转存+移动+整理"""
    try:
        return await receive_share_task(
            req.share_url,
            req.receive_code,
            req.target_dir_id,
            req.filter_rules,
        )
    except TransferServiceError as exc:
        _raise_transfer_http_error(exc)


@router.get("/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """列出转存整理任务"""
    return await list_transfer_tasks(status=status, limit=limit)


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: str):
    """获取任务详情，含操作日志"""
    try:
        return await get_transfer_task_detail(task_id)
    except TransferServiceError as exc:
        _raise_transfer_http_error(exc)


@router.post("/organize/run")
async def manual_organize(temp_dir_id: str):
    """手动触发整理指定的临时目录"""
    try:
        return await run_manual_organize_task(temp_dir_id)
    except TransferServiceError as exc:
        _raise_transfer_http_error(exc)


@router.get("/organize/tasks")
async def list_organize_tasks(limit: int = 50):
    """列出整理任务"""
    return await list_transfer_tasks(limit=limit)


@router.get("/organize/tasks/{task_id}")
async def get_organize_task_detail(task_id: str):
    """获取整理任务详情"""
    return await get_task_detail(task_id)


@router.post("/rollback/{task_id}")
async def rollback_task(task_id: str):
    """一键还原指定任务的所有操作"""
    try:
        return await start_rollback_task(task_id)
    except TransferServiceError as exc:
        _raise_transfer_http_error(exc)


@router.get("/rollback/{task_id}/preview")
async def preview_rollback(task_id: str):
    """预览还原操作（不执行）"""
    return await preview_rollback_task(task_id)


@router.get("/categories")
async def get_categories():
    """返回当前分类配置"""
    return get_transfer_categories()