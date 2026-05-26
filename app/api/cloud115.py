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

@router.get("/play/{pickcode}")
@router.head("/play/{pickcode}")
@router.get("/play/{pickcode}/{filename:path}")
@router.head("/play/{pickcode}/{filename:path}")
async def play_video(pickcode: str, request: Request, filename: str = ""):
    """获取视频直链 (智能代理中转或302跳转)"""
    method = request.method
    client_ip = request.client.host if request.client else "Unknown IP"
    
    player_ua = request.headers.get("user-agent", "Unknown")
    
    # 核心风控拦截优化：
    # 飞牛刮削器(Lavf/60.3.100)会疯狂拉取切片导致 115 封号风控，必须拦截返回 0 字节。
    # Vidhub(Lavf/59.27.100)是真实播放器，115 CDN 原生支持该 UA 直连，必须放行走 302！
    is_scraper = False
    if "Go-http-client" in player_ua:
        is_scraper = True
    elif "Lavf/" in player_ua:
        import re
        match = re.search(r"Lavf/(\d+)", player_ua)
        if match and int(match.group(1)) >= 60:
            is_scraper = True
            
    if is_scraper:
        logger.info(f"已拦截疑似刮削器探针: pickcode={pickcode} filename={filename} method={method} ua={player_ua}")
        from fastapi.responses import Response
        return Response(content=b"", status_code=200, media_type="video/mp4")
        
    if "|" in pickcode:
        import urllib.parse
        encoded_name = urllib.parse.quote(filename)
        strm_content = f"{request.base_url.rstrip('/')}/api/v1/115/play/{pickcode.split('|')[0]}/{encoded_name}"
        config = get_config()
        if config.cloud115.play_ua:
            strm_content += f"|User-Agent={config.cloud115.play_ua}"
        pickcode = pickcode.split("|")[0]
        
    config = get_config()
    target_ua = config.cloud115.play_ua
    
    # 获取直链并执行 302 跳转。
    # 彻底废弃所有形式的反代（Nginx/Python内存池），因为 115 WAF 对任何切片拉取都极度敏感。
    # 根据用户反馈，直接 302 跳转 + 精准拦截刮削器 是最完美、最不会触发风控的方案！
    request_ua = target_ua if target_ua else player_ua
    url = await client_115.get_download_url(pickcode, user_agent=request_ua)
    
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (115 API UA: {request_ua})")
    return RedirectResponse(url=url, status_code=302)
