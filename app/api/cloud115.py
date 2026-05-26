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
    
    # 核心风控拦截：使用欺骗式断流代理，因此不再需要直接拦截 Lavf。
    # 所有的探针都将通过底层的 httpx Keep-Alive 池被处理，无论是刮削器还是 Vidhub 都能正常工作且不触发风控！
        
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

    # 强制为被 115 CDN WAF 黑名单的 UA（如 Lavf/FFmpeg）开启流式代理，即使未配置 play_ua
    if not target_ua and ("Lavf/" in player_ua or "FFmpeg" in player_ua):
        if client_115.client:
            target_ua = client_115.client.headers.get("user-agent", "Mozilla/5.0")
        else:
            target_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        logger.info(f"🛡️ [WAF Bypass] Auto-enabling spoofed proxy mode for blacklisted UA: {player_ua}")
    
    # 如果用户没有配置伪装UA，采用最原生的 302 跳转模式
    if not target_ua:
        url = await client_115.get_download_url(pickcode, user_agent=player_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
        logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (Player UA: {player_ua})")
        return RedirectResponse(url=url, status_code=302)
        
    # 如果配置了伪装UA（例如 iPad），必须走流式代理！
    # 因为 302 跳转无法强制播放器改变自身 UA，会导致 115 CDN 校验失败 (403)
    url = await client_115.get_download_url(pickcode, user_agent=target_ua)
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.info(f"🔁 [{method}] Spoofed Drop Proxying {pickcode} via local stream (Spoofed UA: {target_ua})")
    
    import httpx
    # Ensure global client exists for connection pooling
    if not hasattr(request.app.state, "proxy_client"):
        request.app.state.proxy_client = httpx.AsyncClient(
            verify=False, 
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=200, keepalive_expiry=60.0)
        )
    client = request.app.state.proxy_client

    # Parse requested range
    req_range = request.headers.get("range", "bytes=0-")
    start_byte = 0
    if req_range.startswith("bytes="):
        parts = req_range.replace("bytes=", "").split("-")
        if parts[0].isdigit():
            start_byte = int(parts[0])

    if method == "HEAD":
        # For HEAD requests, just pass through to CDN directly and return headers
        proxy_headers = {"User-Agent": target_ua}
        req = client.build_request("HEAD", url, headers=proxy_headers)
        try:
            resp = await client.send(req)
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]}
            await resp.aclose()
            return Response(status_code=resp.status_code, headers=resp_headers)
        except Exception as e:
            logger.error(f"❌ [{method}] HEAD proxy failed: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

    # Force a strict chunk size to guarantee Keep-Alive pool reuse
    CHUNK_SIZE = 2 * 1024 * 1024  # 2MB
    end_byte = start_byte + CHUNK_SIZE - 1

    proxy_headers = {
        "User-Agent": target_ua,
        "Range": f"bytes={start_byte}-{end_byte}"
    }

    try:
        resp = await client.get(url, headers=proxy_headers, follow_redirects=True)
        if resp.status_code == 416:
            return Response(status_code=416, content=await resp.aread())
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"❌ [{method}] Proxy connection failed: {e}")
        raise HTTPException(status_code=502, detail="Proxy connection failed")

    # 核心：完全读取这个分块到内存！因为只有把 HTTP body 完全消费掉，底层 TCP socket 才会安全返回给 httpx 的长连接池！
    # 这样不管客户端（如 Vidhub）随后如何暴力切断连接，都不会引发针对 115 CDN 的重复 TLS 握手！
    body = await resp.aread()

    # 从 CDN 的 Content-Range 头中解析出实际的总文件大小
    # 格式通常是：bytes 0-2097151/50000000
    total_size = "*"
    cdn_cr = resp.headers.get("Content-Range", "")
    if "/" in cdn_cr:
        total_size = cdn_cr.split("/")[1]

    # 清理无用头
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-length", "content-range", "content-disposition", "content-type"]}
    
    import mimetypes
    content_type, _ = mimetypes.guess_type(filename)
    resp_headers["Content-Type"] = content_type or "video/mp4"
    resp_headers["Accept-Ranges"] = "bytes"
    
    if total_size != "*":
        total_int = int(total_size)
        # 核心欺骗：伪造 Content-Range 和 Content-Length，让播放器以为我们会发送完整的 50GB
        resp_headers["Content-Range"] = f"bytes {start_byte}-{total_int - 1}/{total_size}"
        resp_headers["Content-Length"] = str(total_int - start_byte)
    else:
        resp_headers["Content-Length"] = str(len(body))

    async def spoof_streamer():
        # 一次性吐出缓冲的 2MB 数据，然后生成器直接结束！
        yield body
        # 生成器结束导致 FastAPI 主动断开与播放器的连接。
        # 播放器收到断连后会认为网络波动，从而发起下一段 Range 请求（完美触发断点续传）。
        logger.debug(f"🔪 Dropped connection to client after sending {len(body)} bytes. Waiting for retry...")

    from fastapi.responses import StreamingResponse
    return StreamingResponse(spoof_streamer(), status_code=206, headers=resp_headers)
