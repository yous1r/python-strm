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
        logger.info("[115 API] Cookie updated successfully")
        return {"status": "success", "message": "Cookie updated"}
    logger.error("[115 API] Failed to update cookie: Invalid cookie provided")
    raise HTTPException(status_code=400, detail="Invalid cookie")

@router.get("/user")
async def get_user():
    """获取用户信息"""
    info = await auth_manager.get_user_info()
    if "error" in info:
        logger.error(f"[115 API] Failed to get user info: {info['error']}")
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.get("/qr/token")
async def get_qr_token():
    """获取登录二维码"""
    info = await auth_manager.get_qr_token()
    if "error" in info:
        logger.error(f"[115 API] Failed to get QR token: {info['error']}")
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.post("/qr/status")
async def check_qr_status(payload: dict):
    """检查二维码状态"""
    info = await auth_manager.check_qr_status(payload)
    if "error" in info:
        logger.error(f"[115 API] QR status check error: {info['error']}")
        raise HTTPException(status_code=400, detail=info["error"])
    return info

@router.get("/files")
async def list_files(dir_id: str = '0', limit: int = 100, offset: int = 0):
    """获取文件列表"""
    res = await client_115.list_files(dir_id, limit, offset)
    if "error" in res:
        logger.error(f"[115 API] Failed to list files (dir_id={dir_id}): {res['error']}")
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@router.get("/dirs")
async def list_dirs(dir_id: str = '0'):
    """获取纯文件夹列表 (用于目录选择器)"""
    res = await client_115.list_dirs(dir_id)
    if "error" in res:
        logger.error(f"[115 API] Failed to list dirs (dir_id={dir_id}): {res['error']}")
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
    
    logger.info(f"[115 API] Requesting play_video: pickcode={pickcode}, client_ip={client_ip}, method={method}, UA={player_ua}, client={client_param}")
    logger.debug(f"[115 API] Request headers: {dict(request.headers)}")
    
    # === 飞牛/Emby 探针处理逻辑 ===
    # - 直连飞牛（无 client 参数，纯刮削/扫库）：直接返回 200，不代理 CDN
    # - 代理播放触发（有 client 参数）：token bucket 允许少量 CDN 请求，让 ffprobe 获取媒体信息
    is_probe = False
    if "Go-http-client" in player_ua:
        is_probe = True
    elif "Lavf/" in player_ua and client_param != "vidhub":
        is_probe = True

    if is_probe:
        # 直连飞牛 → 直接 200
        if not client_param:
            logger.debug(f"[飞牛探针] pickcode={pickcode} 直连扫库 → 直接返回 200")
            return Response(status_code=200)

        # 代理播放 → token bucket 限流，允许少量 CDN 访问
        import time
        if not hasattr(play_video, '_token_bucket'):
            play_video._token_bucket = {}

        now = time.time()
        bucket = play_video._token_bucket.get(pickcode, {"tokens": 5, "last_refill": now, "window": 30})

        # 超过窗口期重置 token
        if now - bucket["last_refill"] > bucket["window"]:
            bucket["tokens"] = 5
            bucket["last_refill"] = now

        if bucket["tokens"] <= 0:
            logger.debug(f"[飞牛探针] pickcode={pickcode} token 耗尽 → 返回 200")
            play_video._token_bucket[pickcode] = bucket
            return Response(status_code=200)

        bucket["tokens"] -= 1
        play_video._token_bucket[pickcode] = bucket

        logger.info(f"[飞牛探针] pickcode={pickcode} method={method} range={request.headers.get('range')} tokens_left={bucket['tokens']}")

        # 代理少量 CDN 请求
        config = get_config()
        browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        url = await client_115.get_download_url(pickcode, user_agent=browser_ua)
        if not url:
            return Response(status_code=404)

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
            logger.warning(f"[飞牛探针] CDN请求异常: {repr(e)}")
            return Response(status_code=200)

    # ============== 获取 115 直链 (真实播放) ==============
    # 如果用户配置了统一伪装UA，则优先使用；否则使用请求本身的UA
    config = get_config()
    target_ua = config.cloud115.play_ua
    request_ua = target_ua if target_ua else player_ua
    
    url = await client_115.get_download_url(pickcode, user_agent=request_ua)
    if not url:
        logger.error(f"[115 API] Failed to get download URL for pickcode={pickcode}")
        raise HTTPException(status_code=404, detail="Download URL not found")

    logger.info(f"[115 API] Got download URL for pickcode={pickcode}: {url}")

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
