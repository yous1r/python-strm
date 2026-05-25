from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.media.organizer import organizer
from app.core.cloud115.client import client_115

router = APIRouter(prefix="/api/v1/organize", tags=["影视整理"])

class OrganizeReq(BaseModel):
    cloud_type: str
    source_dir_ids: list[str]
    target_base_dir_id: str

@router.post("/start")
async def start_organize(req: OrganizeReq):
    results = []
    
    if req.cloud_type == "115":
        for src_dir in req.source_dir_ids:
            res = await client_115.list_files(dir_id=src_dir)
            if "error" in res:
                # Optionally continue instead of throwing to process remaining folders
                continue
            for item in res.get("items", []):
                # 处理目录 (cid) 或文件 (fid)
                if "cid" in item or "fid" in item:
                    org_res = await organizer.organize_item(client_115, item, req.target_base_dir_id)
                    results.append(org_res)
    elif req.cloud_type == "123":
        from app.core.cloud123.client import client_123
        for src_dir in req.source_dir_ids:
            res = await client_123.list_files(parent_id=src_dir)
            if "error" in res:
                continue
            for item in res.get("items", []):
                # 123pan 约定 type=0 是文件, type=1 是目录 (或相反, 需要根据文档)
                # 我们统一传入 organize_item 让其处理
                item['n'] = item.get("file_name", "")
                item['fid'] = str(item.get("file_id", ""))
                org_res = await organizer.organize_item(client_123, item, req.target_base_dir_id)
                results.append(org_res)
    else:
        raise HTTPException(status_code=400, detail="Unsupported cloud type")
        
    return {
        "status": "success",
        "total": len(results),
        "details": results
    }
