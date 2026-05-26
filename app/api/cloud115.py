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
    
    # 飞牛后端探测流程：
    # 1. Go-http-client HEAD → 获取文件元信息 → 走正常 302 即可
    # 2. Lavf/60.x GET → 读取文件头来识别编码 → 必须反代！
    #    因为 115 CDN 黑名单封杀了 Lavf UA，302 跳转后 CDN 直接拒绝，导致无限重试。
    #    解决：用浏览器 UA 从 CDN 拉取数据，透传给 Lavf。Lavf 只读几 KB 头部就断开。
    is_lavf_probe = False
    if "Lavf/" in player_ua:
        import re as _re
        lavf_match = _re.search(r"Lavf/(\d+)", player_ua)
        if lavf_match and int(lavf_match.group(1)) >= 60:
            is_lavf_probe = True
    
    if is_lavf_probe:
        # 防止无限重试：同一 pickcode 的 Lavf 探针最多处理 3 次
        if not hasattr(play_video, '_lavf_retry_count'):
            play_video._lavf_retry_count = {}
        count = play_video._lavf_retry_count.get(pickcode, 0)
        if count >= 3:
            logger.warning(f"🚫 [飞牛探针] pickcode={pickcode} 已重试{count}次，返回空响应终止循环")
            return Response(content=b"", status_code=200, headers={"Content-Type": "video/mp4"})
        play_video._lavf_retry_count[pickcode] = count + 1
        
        logger.info(f"📡 [飞牛探针-反代] pickcode={pickcode} method={method} ua={player_ua} attempt={count+1}")
        browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        url = await client_115.get_download_url(pickcode, user_agent=browser_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
        
        logger.info(f"📡 [飞牛探针-反代] CDN URL (前100字符): {url[:100]}...")
        
        # 非流式反代：只拉取前 2MB，足够 Lavf 识别编码格式
        proxy_headers = {
            "User-Agent": browser_ua,
            "Range": "bytes=0-2097151"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as proxy_client:
                cdn_resp = await proxy_client.get(url, headers=proxy_headers)
                
                logger.info(f"📡 [飞牛探针-反代] CDN响应: status={cdn_resp.status_code}, "
                           f"content-type={cdn_resp.headers.get('content-type', 'N/A')}, "
                           f"content-length={cdn_resp.headers.get('content-length', 'N/A')}, "
                           f"body_size={len(cdn_resp.content)}")
                
                if cdn_resp.status_code >= 400:
                    logger.error(f"📡 [飞牛探针-反代] CDN返回错误! body前200字节: {cdn_resp.content[:200]}")
                    return Response(content=b"", status_code=200, headers={"Content-Type": "video/mp4"})
                
                # 关键：返回 200（而非 206）且不带 Content-Range！
                # 如果返回 206 + Content-Range，Lavf 会知道文件实际有几十 GB，
                # 然后尝试请求后续 Range 片段，但每次都只拿到相同的前 2MB，陷入死循环。
                # 返回 200 让 Lavf 以为这 2MB 就是完整文件，解析元数据后即可成功。
                resp_headers = {
                    "Content-Type": cdn_resp.headers.get("content-type", "video/mp4"),
                    "Content-Length": str(len(cdn_resp.content)),
                    "Accept-Ranges": "bytes"
                }
                
                return Response(content=cdn_resp.content, status_code=200, headers=resp_headers)
                
        except Exception as e:
            logger.error(f"📡 [飞牛探针-反代] CDN请求异常: {repr(e)}")
            raise HTTPException(status_code=502, detail="CDN proxy failed")
    
    if "Go-http-client" in player_ua:
        logger.info(f"📡 [飞牛探针-302] pickcode={pickcode} method={method} ua={player_ua} → 正常302跳转")
        
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
    request_ua = target_ua if target_ua else player_ua
    url = await client_115.get_download_url(pickcode, user_agent=request_ua)
    
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    # 针对某些对 302 跳转支持不佳的播放器（特别是 Vidhub），使用 M3U8 播放列表伪装直链。
    needs_m3u8 = False
    if "VidHub" in player_ua or "Infuse" in player_ua or ("Lavf/" in player_ua and "Lavf/60." not in player_ua):
        needs_m3u8 = True

    if needs_m3u8:
        logger.info(f"🔄 [{method}] Returning M3U8 playlist for {pickcode} to bypass 302 (Player UA: {player_ua})")
        m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n"
        return Response(content=m3u8_content, status_code=200, media_type="application/vnd.apple.mpegurl")
    else:
        logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (Player UA: {player_ua})")
        return RedirectResponse(url=url, status_code=302)
