from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
import httpx
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
    client_param = request.query_params.get("client")
    
    # 核心风控与播放兼容逻辑：
    # 飞牛后台刮削器(Lavf/60.3.100)会扫描整个媒体库拉取切片，极易导致 115 封号风控，必须拦截(返回0字节)。
    # 但是，Vidhub 播放 MKV 时使用的内核同样是 Lavf/60.3.100！
    # 为了区分它们，我们在 standalone_proxy (即用户点击播放时生成的动态信息) 中加入了 ?client=vidhub。
    # 只有不带 client 参数的 Lavf 请求才被认为是后台定时刮削器，必须拦截。
    # 对于带有 client 参数的真实播放请求，115 CDN 实际上并不封杀它，我们可以安全地放行 302 跳转！
    is_scraper = False
    if "Go-http-client" in player_ua:
        is_scraper = True
    elif "Lavf/" in player_ua and client_param != "vidhub":
        # 如果是 Lavf 且没有通过真实播放端(PlaybackInfo)下发，认为是后台刮削器
        is_scraper = True

    if is_scraper:
        logger.info(f"🚫 [刮削器拦截] 已拦截疑似后台媒体刮削: pickcode={pickcode} filename={filename} method={method} ua={player_ua}")
        return Response(content=b"", status_code=200, media_type="video/mp4")

    # ============== 获取 115 直链 ==============
    # 如果用户配置了统一伪装UA，则优先使用；否则使用请求本身的UA
    config = get_config()
    target_ua = config.cloud115.play_ua
    request_ua = target_ua if target_ua else player_ua
    
    url = await client_115.get_download_url(pickcode, user_agent=request_ua)
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")

    # 针对某些对 302 跳转支持不佳的播放器（例如 Vidhub 的部分旧模式或 Infuse），使用 M3U8 播放列表伪装直链。
    # 注意：我们现在放开了对 Lavf/60. 的限制，因为已经通过 client=vidhub 排除了刮削器风险。
    needs_m3u8 = False
    if "VidHub" in player_ua or "Infuse" in player_ua:
        needs_m3u8 = True

    if needs_m3u8:
        logger.info(f"🔄 [{method}] Returning M3U8 playlist for {pickcode} to bypass 302 (Player UA: {player_ua})")
        m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n"
        return Response(content=m3u8_content, status_code=200, media_type="application/vnd.apple.mpegurl")
    else:
        logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (Player UA: {player_ua})")
        return RedirectResponse(url=url, status_code=302)
