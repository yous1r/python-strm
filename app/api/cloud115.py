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
    
    # === 飞牛/Emby 探针处理逻辑 ===
    # 飞牛后台的媒体刮削器和播放前的动态探测都会使用 Lavf(ffprobe) 和 Go-http-client。
    # 它们请求的 URL 是原始的 .strm 地址（不带 client 参数）。
    # 对于带有 client=vidhub 的真实播放请求，我们直接放行走底部的 302 跳转。
    is_probe = False
    if "Go-http-client" in player_ua:
        is_probe = True
    elif "Lavf/" in player_ua and client_param != "vidhub":
        is_probe = True

    if is_probe:
        import time
        if not hasattr(play_video, '_probe_tracker'):
            play_video._probe_tracker = {}
            
        now = time.time()
        tracker = play_video._probe_tracker.get(pickcode, {"count": 0, "last_time": 0})
        
        # 超过 30 秒没有请求，说明是新的一轮探测（例如用户点击播放引发的探测），重置计数器
        if now - tracker["last_time"] > 30:
            tracker["count"] = 0
            
        tracker["count"] += 1
        tracker["last_time"] = now
        play_video._probe_tracker[pickcode] = tracker
        
        # 如果 30 秒内连续请求超过 5 次，说明 ffprobe 找不到索引开始进行 8GB 的顺序扫描了！
        # 必须返回 416 (Range Not Satisfiable) 模拟文件尾，让 ffprobe 提前结束探测，防止 115 封号。
        if tracker["count"] > 5:
            logger.warning(f"🚫 [刮削器阻断] pickcode={pickcode} 短期内请求过多({tracker['count']}次)，返回 416 模拟 EOF 中断扫描")
            from fastapi.responses import Response
            return Response(status_code=416)

        logger.info(f"📡 [飞牛探针代理] pickcode={pickcode} method={method} range={request.headers.get('range')} ua={player_ua} attempt={tracker['count']}")
        
        # 必须走代理返回数据，否则 ffprobe 无法获取视频头信息，会导致 PlaybackInfo 生成失败
        config = get_config()
        browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        url = await client_115.get_download_url(pickcode, user_agent=browser_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
            
        proxy_headers = {"User-Agent": browser_ua}
        if "range" in request.headers:
            proxy_headers["Range"] = request.headers["range"]
            
        proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10))
        try:
            req = proxy_client.build_request(method, url, headers=proxy_headers)
            cdn_resp = await proxy_client.send(req, stream=True, follow_redirects=True)
            
            resp_headers = {k: v for k, v in cdn_resp.headers.items() if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
            resp_headers["Accept-Ranges"] = "bytes"
            
            from fastapi.responses import StreamingResponse
            async def stream_from_cdn():
                try:
                    async for chunk in cdn_resp.aiter_bytes(chunk_size=65536):
                        yield chunk
                except Exception:
                    pass
                finally:
                    await cdn_resp.aclose()
                    await proxy_client.aclose()
                    
            return StreamingResponse(stream_from_cdn(), status_code=cdn_resp.status_code, headers=resp_headers)
        except Exception as e:
            await proxy_client.aclose()
            logger.error(f"📡 [飞牛探针代理] CDN请求异常: {repr(e)}")
            raise HTTPException(status_code=502, detail="CDN proxy failed")

    # ============== 获取 115 直链 (真实播放) ==============
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
