from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.core.emby.manager import emby_manager
from app.core.emby.proxy import emby_proxy
from app.core.emby.client import emby_client

router = APIRouter(prefix="/api/v1/emby", tags=["Emby代理与管理"])

class EmbyInstanceReq(BaseModel):
    name: str
    url: str
    api_key: str

@router.post("/instances")
async def add_instance(req: EmbyInstanceReq):
    """添加Emby实例"""
    return await emby_manager.add_instance(req.name, req.url, req.api_key)

@router.get("/instances")
async def list_instances():
    """列表Emby实例"""
    return await emby_manager.get_instances()

@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    """删除Emby实例"""
    success = await emby_manager.delete_instance(instance_id)
    if not success:
        raise HTTPException(status_code=404, detail="Instance not found")
    return {"status": "success"}

@router.get("/instances/{instance_id}/series")
async def get_instance_series(instance_id: str):
    """获取实例下的剧集/电影库"""
    return await emby_client.get_series(instance_id)

@router.get("/instances/{instance_id}/series/{series_id}/episodes")
async def get_instance_episodes(instance_id: str, series_id: str):
    """获取指定剧集的分集"""
    return await emby_client.get_episodes(instance_id, series_id)

@router.api_route("/{instance_id}/proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def proxy_emby(instance_id: str, path: str, request: Request):
    """Emby 反向代理入口，处理302直链播放"""
    return await emby_proxy.handle_request(instance_id, path, request)
