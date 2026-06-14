"""调试 API：将转存整理管道拆分为独立步骤，逐步测试"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.core.cloud import get_cloud_plugin
from app.core.transfer.classifier import classify, build_archive_path
from app.core.transfer.catalog import ensure_path
from app.core.transfer.scope import init_scope, validate, expand_allowed_dirs
from app.config import get_config
from app.services.cloud115_full_sync_service import cloud115_full_sync_service
from app.services.telegram_background_service import telegram_background_sync_service
from app.services.telegram_history_sync_service import telegram_history_sync_service

router = APIRouter(prefix="/api/v1/debug", tags=["调试"])


class DebugResult(BaseModel):
    step: str
    success: bool
    data: dict = {}
    error: str = ""


@router.post("/cloud115/full-sync")
async def start_cloud115_full_sync():
    result = await cloud115_full_sync_service.start_full_sync(source="debug")
    return {
        "success": True,
        **result,
    }


@router.get("/cloud115/full-sync/{task_id}")
async def get_cloud115_full_sync(task_id: str):
    task = await cloud115_full_sync_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "success": task.get("status") != "failed",
        **task,
        "completed": task.get("status") in {"completed", "failed"},
    }


@router.post("/telegram/latest-transfer")
async def trigger_telegram_latest_transfer():
    result = await telegram_background_sync_service.run_scheduled_sync()
    return {
        "success": result.get("status") != "failed",
        **result,
    }


@router.post("/telegram/history-sync")
async def trigger_telegram_history_sync_debug():
    result = telegram_history_sync_service.queue_history_sync_request(
        telegram_history_sync_service.build_request_from_config(source="debug"),
        name="telegram_history_sync:debug",
    )
    return {
        "success": True,
        **result,
    }


# --- Step 1: 接收分享 ---
@router.post("/step1_share_receive")
async def step1_share_receive(share_url: str, receive_code: str = "", target_dir_id: str = "0"):
    """仅执行 share_receive，返回结果。filter_rules=None 表示接收全部文件，不应用规则过滤。"""
    res = await get_cloud_plugin("115").client.share_receive(share_url, receive_code, target_dir_id, filter_rules=None)
    return DebugResult(
        step="share_receive",
        success=res.get("state", False),
        data={"raw": {k: str(v)[:200] for k, v in res.items()}},
        error=res.get("error", "")
    ).model_dump()


# --- Step 2: 列出目录文件 ---
@router.get("/step2_list_files")
async def step2_list_files(dir_id: str = "0", limit: int = 50):
    """列出指定目录的文件"""
    config = get_config()
    archive_id = getattr(config.transfer, "archive_dir_id", "")
    res = await get_cloud_plugin("115").client.list_files(dir_id, limit=limit)
    files = []
    if not res.get("error"):
        for item in res.get("items", []):
            cid = item.get("cid") or item.get("fid") or ""
            name = item.get("n", "")
            is_dir = "fid" not in item
            if cid:
                files.append({"cid": str(cid), "name": name, "is_dir": is_dir})
    return DebugResult(
        step="list_files",
        success=not res.get("error"),
        data={"dir_id": dir_id, "archive_dir_id": archive_id, "count": len(files), "files": files},
        error=res.get("error", "")
    ).model_dump()


# --- Step 3: 移动文件 ---
class MoveRequest(BaseModel):
    file_cid: str
    source_dir_id: str
    target_dir_id: str
    file_name: str = ""


@router.post("/step3_move_file")
async def step3_move_file(req: MoveRequest):
    """移动单个文件"""
    init_scope(req.source_dir_id, req.target_dir_id, req.source_dir_id)
    if not validate(req.source_dir_id, req.target_dir_id):
        return DebugResult(step="move_file", success=False, error="安全校验失败").model_dump()

    ok = await get_cloud_plugin("115").client.move_files([req.file_cid], req.target_dir_id)
    return DebugResult(
        step="move_file",
        success=ok,
        data={"file": req.file_name, "cid": req.file_cid, "from": req.source_dir_id, "to": req.target_dir_id}
    ).model_dump()


# --- Step 4: 测试分类 ---
@router.get("/step4_classify")
async def step4_classify(file_name: str):
    """测试 TMDB 分类"""
    result = await classify(file_name)
    if not result:
        return DebugResult(step="classify", success=False, error="分类失败").model_dump()
    path_parts = build_archive_path(result)
    return DebugResult(
        step="classify",
        success=True,
        data={
            "category": result.category,
            "subcategory": result.subcategory,
            "title": result.title,
            "year": result.year,
            "tmdb_id": result.tmdb_id,
            "season": result.season,
            "archive_path": "/".join(path_parts)
        }
    ).model_dump()


# --- Step 5: 测试目录创建 ---
class CatalogRequest(BaseModel):
    base_cid: str
    path: str  # 用 / 分隔


@router.post("/step5_ensure_path")
async def step5_ensure_path(req: CatalogRequest):
    """测试逐级创建目录"""
    path_parts = [p for p in req.path.split("/") if p]
    try:
        result_cid = await ensure_path(req.base_cid, path_parts)
        return DebugResult(
            step="ensure_path",
            success=result_cid is not None,
            data={"base_cid": req.base_cid, "path": req.path, "result_cid": result_cid or "N/A"}
        ).model_dump()
    except Exception as e:
        return DebugResult(step="ensure_path", success=False, error=str(e)).model_dump()


# --- Step 6: 列出目录树 ---
@router.get("/step6_list_dirs")
async def step6_list_dirs(dir_id: str = "0"):
    """列出纯目录"""
    res = await get_cloud_plugin("115").client.list_dirs(dir_id)
    return DebugResult(
        step="list_dirs",
        success=not res.get("error"),
        data=res,
        error=res.get("error", "")
    ).model_dump()


# --- Step 7: 创建文件夹 ---
class MkdirRequest(BaseModel):
    parent_id: str
    name: str


@router.post("/step7_mkdir")
async def step7_mkdir(req: MkdirRequest):
    """创建单个文件夹"""
    res = await get_cloud_plugin("115").client.create_folder(req.parent_id, req.name)
    return DebugResult(
        step="mkdir",
        success="id" in res,
        data={"parent_id": req.parent_id, "name": req.name, "result": res}
    ).model_dump()


# --- Step 8: 完整整理一个文件 ---
class OrganizeOneRequest(BaseModel):
    file_cid: str
    file_name: str
    temp_dir_id: str


@router.post("/step8_organize_one")
async def step8_organize_one(req: OrganizeOneRequest):
    """完整整理单个文件：分类→建目录→移动"""
    config = get_config()
    archive_id = getattr(config.transfer, "archive_dir_id", "") or "0"
    steps = []

    if archive_id == "0":
        return DebugResult(step="organize_one", success=False, error="旧版归档目录配置已移除，请使用 STRM 扫描源目录转存").model_dump()

    # 1. 分类
    cr = await classify(req.file_name)
    if not cr:
        return DebugResult(step="organize_one", success=False, error="分类失败").model_dump()
    path_parts = build_archive_path(cr)
    steps.append(f"分类: {cr.category}/{cr.subcategory} → {'/'.join(path_parts)}")

    # 2. 建目录
    try:
        target_cid = await ensure_path(archive_id, path_parts)
    except Exception as e:
        return DebugResult(step="organize_one", success=False, error=f"建目录失败: {e}").model_dump()
    if not target_cid:
        return DebugResult(step="organize_one", success=False, error="建目录返回 None").model_dump()
    steps.append(f"目标目录 cid: {target_cid}")

    # 3. 移动
    init_scope(req.temp_dir_id, archive_id, req.temp_dir_id)
    expand_allowed_dirs(target_cid)
    ok = await get_cloud_plugin("115").client.move_files([req.file_cid], target_cid)
    steps.append(f"移动: {'成功' if ok else '失败'}")

    return DebugResult(
        step="organize_one",
        success=ok,
        data={"file": req.file_name, "cid": req.file_cid, "archive_path": "/".join(path_parts), "target_cid": target_cid, "steps": steps}
    ).model_dump()
