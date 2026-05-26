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

import time
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

class LavfTokenBucket:
    def __init__(self, capacity: float, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.monotonic()
        # 记录最近访问过的 (ip, pickcode) 对，用于豁免播放器的持续探针
        self.recent_pairs = OrderedDict()
        self.max_recent = 1000

    async def consume(self, client_ip: str, pickcode: str) -> bool:
        now = time.monotonic()
        
        pair = (client_ip, pickcode)
        if pair in self.recent_pairs:
            self.recent_pairs[pair] = now
            self.recent_pairs.move_to_end(pair)
            return True

        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_update = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            self.recent_pairs[pair] = now
            if len(self.recent_pairs) > self.max_recent:
                self.recent_pairs.popitem(last=False)
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

    # 核心风控拦截：使用令牌桶算法 + 同视频豁免机制限流扫库探针
    # 返回 429 降速而不是直接阻断，既能让扫库程序减速保护 115 账号，也不会误伤正常的播放
    if any(x in player_ua for x in ["Lavf/", "FFmpeg", "Go-http-client"]):
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

    # 如果播放器已经成功伪装了 UA，则直接 302 跳转，免去代理
    if player_ua == target_ua:
        url = await client_115.get_download_url(pickcode, user_agent=target_ua)
        if not url:
            raise HTTPException(status_code=404, detail="Download URL not found")
        logger.info(f"🔄 [{method}] Player UA matches Target UA, redirecting {pickcode} to CDN directly")
        return RedirectResponse(url=url, status_code=302)

    # ---------------- Nginx X-Accel-Redirect 代理 ----------------
    # 需要代理的情况，向内置 Nginx 下发神级指令 X-Accel-Redirect，由 Nginx 接管底层视频流
    url = await client_115.get_download_url(pickcode, user_agent=target_ua)
    if not url:
        raise HTTPException(status_code=404, detail="Download URL not found")

    logger.info(f"🔁 [{method}] Offloading {pickcode} to Nginx via X-Accel-Redirect (Spoofed UA: {target_ua})")
    
    from fastapi.responses import Response
    resp_headers = {
        "X-Accel-Redirect": "/internal_115_proxy/",
        "X-Target-Url": url,
        "X-Target-Ua": target_ua,
    }
    return Response(content=b"", status_code=200, headers=resp_headers)
