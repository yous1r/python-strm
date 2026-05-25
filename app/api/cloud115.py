from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.cloud115.auth import auth_manager
from app.config import get_config
import httpx

# 全局代理客户端，启用 HTTP/2 极大优化 mpv 的并发探针！
# HTTP/2 支持 RST_STREAM 帧，允许播放器在读取 64KB 后瞬间掐断数据流，同时保持底层 TCP/TLS 连接不断开！
# 这是解决 mpv 狂暴嗅探 mkv 索引导致代理瘫痪的终极武器。
limits = httpx.Limits(max_keepalive_connections=100, max_connections=200, keepalive_expiry=60.0)
proxy_client = httpx.AsyncClient(verify=False, http2=True, limits=limits, timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=10.0))

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

import time
import asyncio
from collections import OrderedDict

class LavfTokenBucket:
    def __init__(self, capacity=5.0, fill_rate=1.0, cache_ttl=30.0):
        self.capacity = capacity
        self.tokens = capacity
        self.fill_rate = fill_rate
        self.timestamp = time.monotonic()
        self.lock = asyncio.Lock()
        self.cache_ttl = cache_ttl
        self.recent_pairs = OrderedDict()

    def _cleanup_cache(self, now):
        while self.recent_pairs:
            _, ts = next(iter(self.recent_pairs.items()))
            if now - ts > self.cache_ttl:
                self.recent_pairs.popitem(last=False)
            else:
                break

    async def consume(self, client_ip: str, pickcode: str) -> bool:
        async with self.lock:
            now = time.monotonic()
            self._cleanup_cache(now)
            
            pair = (client_ip, pickcode)
            if pair in self.recent_pairs:
                # 同一个 IP 请求同一个文件，享受免扣令牌直接放行，应对播放器缓冲时的并发请求
                self.recent_pairs[pair] = now
                self.recent_pairs.move_to_end(pair)
                return True
                
            elapsed = now - self.timestamp
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.timestamp = now
            
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self.recent_pairs[pair] = now
                return True
            return False

lavf_limiter = LavfTokenBucket(capacity=20.0, fill_rate=2.0)

@router.get("/play/{pickcode}")
@router.head("/play/{pickcode}")
@router.get("/play/{pickcode}/{filename:path}")
@router.head("/play/{pickcode}/{filename:path}")
async def play_video(pickcode: str, request: Request, filename: str = ""):
    """获取视频直链 (智能代理中转或302跳转)"""
    method = request.method
    client_ip = request.client.host if request.client else "Unknown IP"
    
    player_ua = request.headers.get("user-agent", "Unknown")

    # 智能风控拦截：使用令牌桶算法 + 同视频豁免机制 限流扫库探针
    # 返回 429 Too Many Requests 既能让扫库程序减速，也不会破坏媒体库缓存
    if any(x in player_ua for x in ["Lavf/", "FFmpeg", "Go-http-client"]):
        # 提取真实的 pickcode（无视后缀）
        clean_pc = pickcode.split('|')[0] if '|' in pickcode else pickcode
        if not await lavf_limiter.consume(client_ip, clean_pc):
            logger.info(f"🚫 [风控保护] 已拦截频繁探针请求: pickcode={clean_pc} filename={filename} method={method} ua={player_ua}")
            from fastapi.responses import Response
            return Response(content=b"Too Many Requests (115 API Protection)", status_code=429, media_type="text/plain")

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
        logger.info(f"🛡️ [WAF Bypass] Auto-enabling proxy mode for blacklisted UA: {player_ua} (Spoofed as API default: {target_ua})")

    # 如果用户没有配置伪装UA，采用最原生的 302 跳转模式
    if not target_ua:
        url = await client_115.get_download_url(pickcode, user_agent=player_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
        logger.info(f"🔄 [{method}] Redirecting {pickcode} to CDN directly (Player UA: {player_ua})")
        return RedirectResponse(url=url, status_code=302)
        
    # 如果播放器已经成功伪装了 UA（比如通过读取 .strm 文件末尾的 |User-Agent= 参数），
    # 此时它的 UA 已经和目标 UA 完全一致，我们就可以直接 302 跳转，免去流式代理的性能损耗！
    if player_ua == target_ua:
        url = await client_115.get_download_url(pickcode, user_agent=target_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
        logger.info(f"🔄 [{method}] Player UA matches Target UA, redirecting {pickcode} to CDN directly")
        return RedirectResponse(url=url, status_code=302)
        
    # 如果配置了伪装UA，但播放器自身不支持修改 UA（导致上报的还是原始 UA），必须走流式代理！
    # 因为 302 跳转无法强制播放器改变自身 UA，会导致 115 CDN 校验失败 (403)
    url = await client_115.get_download_url(pickcode, user_agent=target_ua)
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.info(f"🔁 [{method}] Proxying {pickcode} via local stream (Spoofed UA: {target_ua})")
    
    proxy_headers = {
        "User-Agent": target_ua,
        "Accept-Encoding": "identity",  # 必须禁用压缩，否则 Range 请求的字节偏移量会因 gzip 导致错位，进而引发播放器无限断开重连死循环
    }

    # ---------------- 终极代理优化策略：强制分块与内存缓冲 ----------------
    # 核心痛点：mpv 嗅探 MKV 索引时会发起开放式 Range 请求 (bytes=X-)，读取 64KB 后立即掐断连接。
    # 这会导致底层 httpx 的连接被强制 Cancel，进而摧毁 TLS 握手！20 次探针就会耗时数秒导致起播超时。
    # 解决方案：我们将所有代理请求强制限制在最大 2MB！
    # 1. 拦截播放器的 Range 请求。
    # 2. 如果是开放式请求 (bytes=X-) 或跨度大于 2MB，我们强制改写为 bytes=X-(X+2MB)。
    # 3. 将这 2MB 数据 *完整* 读取到内存中 (耗时极短)！
    # 4. 此时 115 CDN 的连接被完美消费完毕，安全放回 Keep-Alive 连接池！
    # 5. 最后，将内存中的 2MB 数据作为普通 Response 返回给播放器。播放器随时掐断都不会影响底层的 TLS 连接池！
    
    start_byte = 0
    end_byte = None
    CHUNK_LIMIT = 2 * 1024 * 1024  # 2MB 最佳平衡点 (兼顾 TTFB 与 连续播放时的吞吐量)
    
    if "range" in request.headers:
        range_str = request.headers["range"]
        if range_str.startswith("bytes="):
            parts = range_str[6:].split("-")
            try:
                if parts[0]:
                    start_byte = int(parts[0])
                if len(parts) > 1 and parts[1]:
                    end_byte = int(parts[1])
            except ValueError:
                pass

    if end_byte is None or (end_byte - start_byte > CHUNK_LIMIT):
        # 强制限制最大请求范围为 2MB
        forced_end = start_byte + CHUNK_LIMIT - 1
        proxy_headers["Range"] = f"bytes={start_byte}-{forced_end}"
    else:
        proxy_headers["Range"] = request.headers["range"]

    if "if-range" in request.headers:
        proxy_headers["If-Range"] = request.headers["if-range"]

    req = proxy_client.build_request(method, url, headers=proxy_headers)

    if method == "HEAD":
        try:
            resp = await proxy_client.send(req)
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]}
            return Response(status_code=resp.status_code, headers=resp_headers)
        except Exception as e:
            logger.error(f"❌ [{method}] HEAD proxy failed: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

    try:
        resp = await proxy_client.send(req, stream=True, follow_redirects=True)
    except Exception as e:
        logger.error(f"❌ [{method}] Proxy connection failed: {e}")
        raise HTTPException(status_code=502, detail="Proxy connection failed")

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition", "content-type"]}

    import mimetypes
    import urllib.parse

    decoded_filename = urllib.parse.unquote(filename)
    clean_filename = decoded_filename.split("|")[0]

    content_type, _ = mimetypes.guess_type(clean_filename)
    resp_headers["Content-Type"] = content_type or "application/octet-stream"
    resp_headers["Accept-Ranges"] = "bytes"
    
    try:
        # 直接将整个 chunk (最多 2MB) 全部读入内存
        body = await resp.aread()
        # 将内存中的数据一次性返回。如果播放器只读了 64KB 就关闭连接，Uvicorn 会自动终止发送，且不影响 115 CDN 连接
        return Response(content=body, status_code=resp.status_code, headers=resp_headers)
    except Exception as e:
        logger.error(f"❌ [{method}] Read proxy chunk failed: {e}")
        raise HTTPException(status_code=502, detail="Proxy read failed")
    finally:
        await resp.aclose()
