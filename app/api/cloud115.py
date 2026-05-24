from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.cloud115.auth import auth_manager

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

@router.get("/play/{pickcode}")
@router.head("/play/{pickcode}")
async def play_video(pickcode: str, request: Request):
    """获取视频直链并302跳转 (这是STRM文件指向的地址)"""
    method = request.method
    ua = request.headers.get("user-agent", "Unknown")
    client_ip = request.client.host if request.client else "Unknown IP"
    
    # 伪装为 iPad UA 从而规避风控告警，因为飞牛等播放器的原生 UA 容易触发 115 风控
    ipad_ua = "Mozilla/5.0 (iPad; CPU OS 13_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.4 Mobile/15E148 Safari/604.1"
    
    logger.info(f"▶️ [{method}] Playback requested for {pickcode} from {client_ip} (Player UA: {ua})")
    
    url = await client_115.get_download_url(pickcode, user_agent=ipad_ua)
    if not url:
        logger.error(f"❌ [{method}] Playback failed: No URL returned for {pickcode}")
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.debug(f"🔄 [{method}] Redirecting {pickcode} to: {url[:100]}...")
    return RedirectResponse(url=url, status_code=302)
