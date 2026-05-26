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
    
    # 飞牛后端探测流程：
    # 1. Go-http-client HEAD → 获取文件元信息（Content-Type, Content-Length 等）
    # 2. Lavf/60.x GET → 读取文件头部几 KB 来识别视频编码格式
    # 两步都必须成功，飞牛才会告知播放器"此文件可以直连播放"。
    # 如果返回空响应来拦截，飞牛会无限重试（疯狂拉取），反而更糟。
    # 正确做法：放行所有探测，返回正常的 302 跳转到 115 CDN。
    # 探针只读几 KB 文件头即可完成，对 115 CDN 流量影响极小。
    is_fnos_probe = False
    if "Lavf/" in player_ua:
        import re
        lavf_match = re.search(r"Lavf/(\d+)", player_ua)
        if lavf_match and int(lavf_match.group(1)) >= 60:
            is_fnos_probe = True
    if "Go-http-client" in player_ua:
        is_fnos_probe = True
        
    if is_fnos_probe:
        logger.info(f"📡 [飞牛探针] pickcode={pickcode} method={method} ua={player_ua} → 放行302跳转")
        
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
        
    # 针对某些对 302 跳转支持不佳的播放器（特别是 Vidhub），使用 M3U8 播放列表伪装直链。
    # 这样播放器在解析 M3U8 时，会自动带着原始的 Header 去请求 115 CDN，完全避免 302 丢 Header 的坑。
    needs_m3u8 = False
    if "VidHub" in player_ua or "Infuse" in player_ua or ("Lavf/" in player_ua and "Lavf/60." not in player_ua):
        needs_m3u8 = True

    if needs_m3u8:
        logger.info(f"🔄 [{method}] Returning M3U8 playlist for {pickcode} to bypass 302 (Player UA: {player_ua})")
        m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n"
        from fastapi.responses import Response
        return Response(content=m3u8_content, status_code=200, media_type="application/vnd.apple.mpegurl")
    else:
        logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (Player UA: {player_ua})")
        return RedirectResponse(url=url, status_code=302)
