from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.media.organizer import organizer
from app.core.cloud115.client import client_115

router = APIRouter(prefix="/api/v1/organize", tags=["影视整理"])

class OrganizeReq(BaseModel):
    cloud_type: str
    source_dir_ids: list[str]
    target_base_dir_id: str

async def _organize_for_cloud(cloud_type: str, source_dir_ids: list[str], target_base_dir_id: str) -> list[dict]:
    results = []
    if cloud_type == "115":
        for src_dir in source_dir_ids:
            res = await client_115.list_files(dir_id=src_dir)
            if "error" in res:
                continue
            for item in res.get("items", []):
                if "cid" in item or "fid" in item:
                    org_res = await organizer.organize_item(client_115, item, target_base_dir_id)
                    results.append(org_res)
    elif cloud_type == "123":
        from app.core.cloud123.client import client_123
        for src_dir in source_dir_ids:
            res = await client_123.list_files(parent_id=src_dir)
            if "error" in res:
                continue
            for item in res.get("items", []):
                item['n'] = item.get("file_name", "")
                item['fid'] = str(item.get("file_id", ""))
                org_res = await organizer.organize_item(client_123, item, target_base_dir_id)
                results.append(org_res)
    else:
        raise ValueError("Unsupported cloud type")
    return results

@router.post("/start")
async def start_organize(req: OrganizeReq):
    try:
        results = await _organize_for_cloud(req.cloud_type, req.source_dir_ids, req.target_base_dir_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {
        "status": "success",
        "total": len(results),
        "details": results
    }

@router.post("/start-all")
async def start_all_organize():
    from app.config import get_config
    config = get_config()
    
    # 收集已开启整理功能的网盘任务
    tasks = []
    
    if config.organize.cloud115.enabled:
        tasks.append({
            "cloud_type": "115",
            "source_dir_ids": config.organize.cloud115.source_dirs,
            "target_base_dir_id": config.organize.cloud115.target_dir
        })
        
    # Future support for 123
    # if config.organize.cloud123.enabled: ...

    if not tasks:
        raise HTTPException(status_code=400, detail="需开启整理功能")
        
    all_results = []
    for task in tasks:
        # 跳过未配置目录的任务
        if not task["source_dir_ids"] or not task["target_base_dir_id"]:
            continue
            
        res = await _organize_for_cloud(task["cloud_type"], task["source_dir_ids"], task["target_base_dir_id"])
        all_results.extend(res)
        
    return {
        "status": "success",
        "total": len(all_results),
        "details": all_results
    }

