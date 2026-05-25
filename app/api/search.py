from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from loguru import logger
from app.core.search.pansou import pansou_client
from app.core.cloud115.client import client_115
from app.core.sync.engine import sync_engine
import asyncio

router = APIRouter(prefix="/search", tags=["search"])

class TransferRequest(BaseModel):
    url: str # 磁力链 或 115 分享链接
    target_dir_id: str
    receive_code: Optional[str] = ""
    cloud_type: Optional[str] = "115"

class PluginUpdateRequest(BaseModel):
    enabled_plugins: List[str]

async def poll_offline_task(info_hash: str, target_dir_id: str):
    """后台轮询监控离线下载任务进度"""
    logger.info(f"Started polling offline task: {info_hash}")
    max_retries = 720 # 轮询时间上限，假设一次10秒，总计 2 小时
    
    for _ in range(max_retries):
        await asyncio.sleep(10)
        tasks = await client_115.get_offline_tasks()
        task_info = next((t for t in tasks if t.get("info_hash") == info_hash), None)
        
        if not task_info:
            logger.warning(f"Offline task {info_hash} disappeared.")
            return
            
        status = task_info.get("status")
        percentDone = task_info.get("percentDone", 0)
        
        logger.debug(f"Task {info_hash} status: {status}, progress: {percentDone}%")
        
        if status == 2 or percentDone == 100: # status 2 通常代表成功
            logger.success(f"Offline task {info_hash} completed! Triggering sync engine...")
            # 触发洗版与 STRM 提取
            await sync_engine.run_sync_task()
            return
        elif status == -1:
            logger.error(f"Offline task {info_hash} failed.")
            return

PLUGIN_MAPPING = {
    "alupan": "阿里云盘",
    "quark4k": "夸克4K",
    "pansearch": "PanSearch",
    "huban": "虎斑网盘",
    "nyaa": "Nyaa(动漫BT)",
    "thepiratebay": "海盗湾(BT)",
    "susu": "Susu磁力",
    "ddys": "低端影视",
    "libvio": "LIBVIO影视",
    "javdb": "JavDB",
    "yunso": "云搜",
    "aikanzy": "爱看资源",
    "bixin": "比心网盘",
    "panlian": "盘链",
    "pianku": "片库",
    "quarksoo": "夸克搜",
    "zhizhen": "至臻网盘",
}

@router.get("/plugins")
async def get_plugins():
    """获取支持的插件列表并映射中文名称"""
    raw_plugins = await pansou_client.get_plugins()
    results = []
    for p in raw_plugins:
        results.append({
            "id": p,
            "name": PLUGIN_MAPPING.get(p, p)
        })
    return {"plugins": results}

@router.post("/plugins/update")
async def update_plugins(req: PluginUpdateRequest):
    """热更新插件配置"""
    success = await pansou_client.update_plugins(req.enabled_plugins)
    if success:
        return {"code": 0, "message": "插件配置更新成功"}
    else:
        raise HTTPException(status_code=500, detail="更新插件配置失败")

@router.get("/")
async def search_resources(keyword: str, source_type: str = "all", plugins: Optional[str] = None):
    """使用 Pansou 聚合搜索引擎搜索资源"""
    result = await pansou_client.search(keyword, source_type, plugins)
    return result

@router.post("/transfer")
async def transfer_resource(req: TransferRequest, background_tasks: BackgroundTasks):
    """一键转存分享链接或推送离线下载"""
    url = req.url
    cloud_type = req.cloud_type
    
    if cloud_type == "115":
        if "115.com/s/" in url or "share_code=" in url:
            # 115 分享链接转存
            res = await client_115.share_receive(url, req.receive_code, req.target_dir_id)
            if res.get("state"):
                logger.success(f"Successfully received share link {url}")
                # 分享转存是瞬间完成的，直接触发后台洗版
                background_tasks.add_task(sync_engine.run_sync_task)
                return {"status": "success", "msg": "转存成功，正在后台执行洗版入库"}
            else:
                raise HTTPException(status_code=400, detail=res.get("error", "转存失败"))
                
        elif url.startswith("magnet:?") or url.startswith("http"):
            # 磁力链或种子下载
            res = await client_115.offline_add_url(url, req.target_dir_id)
            if res.get("state"):
                info_hash = res.get("info_hash")
                name = res.get("name", "Unknown")
                logger.info(f"Successfully added offline task {name} ({info_hash})")
                # 开启后台轮询
                if info_hash:
                    background_tasks.add_task(poll_offline_task, info_hash, req.target_dir_id)
                return {"status": "success", "msg": f"已推送到离线下载: {name}"}
            else:
                raise HTTPException(status_code=400, detail=res.get("error", "添加离线任务失败"))
                
        else:
            raise HTTPException(status_code=400, detail="不支持的链接格式")
            
    else:
        raise HTTPException(status_code=400, detail=f"暂不支持一键转存至 {cloud_type} 网盘")
