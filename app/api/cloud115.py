from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.cloud115.auth import auth_manager
from app.config import get_config

router = APIRouter(prefix="/api/v1/115", tags=["115网盘"])

class AuthRequest(BaseModel):
    cookie: str

@router.post("/auth")
async def update_cookie(req: AuthRequest):
    """更新115 Cookie"""
    success = auth_manager.update_cookie(req.cookie)
    if success:
        return {"status": "success", "message": "Cookie updated"}
    raise HTTPException(status_code=400, detail="Invalid cookie")

@router.get("/user")
async def get_user():
    """获取用户信息"""
    info = await auth_manager.get_user_info()
    if "error" in info:
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.get("/qr/token")
async def get_qr_token():
    """获取登录二维码"""
    info = await auth_manager.get_qr_token()
    if "error" in info:
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.post("/qr/status")
async def check_qr_status(payload: dict):
    """检查二维码状态"""
    info = await auth_manager.check_qr_status(payload)
    if "error" in info:
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.get("/files")
async def list_files(dir_id: str = '0', limit: int = 100, offset: int = 0):
    """获取文件列表"""
    res = await client_115.list_files(dir_id, limit, offset)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@router.get("/dirs")
async def list_dirs(dir_id: str = '0'):
    """获取纯文件夹列表 (用于目录选择器)"""
    res = await client_115.list_dirs(dir_id)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@router.get("/play/{pickcode:path}")
@router.head("/play/{pickcode:path}")
async def play_video(pickcode: str, request: Request):
    """获取视频直链并302跳转 (兼容带 |User-Agent 的请求)"""
    method = request.method
    client_ip = request.client.host if request.client else "Unknown IP"
    
    # 兼容部分播放器将 |User-Agent 当作路径发过来的情况 (修复 404)
    if "|" in pickcode:
        pickcode = pickcode.split("|")[0]
    
    # 读取用户配置的 UA
    config = get_config()
    target_ua = config.cloud115.play_ua
    if not target_ua:
        # 如果未强制配置，使用播放器原始 UA
        target_ua = request.headers.get("user-agent", "Unknown")
        
    logger.info(f"▶️ [{method}] Playback requested for {pickcode} from {client_ip} (Target UA: {target_ua})")
    
    # 请求 115 API 获取绑在该 UA 上的 CDN 直链
    url = await client_115.get_download_url(pickcode, user_agent=target_ua)
    if not url:
        logger.error(f"❌ [{method}] Playback failed: No URL returned for {pickcode}")
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.debug(f"🔄 [{method}] Redirecting {pickcode} to: {url[:100]}...")
    return RedirectResponse(url=url, status_code=302)
