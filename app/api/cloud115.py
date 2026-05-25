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
    
    # 核心风控拦截：拦截飞牛影视等播放器的内置 FFmpeg(Lavf) 媒体信息提取探针。
    # 这种探针会疯狂发送 GET/HEAD 请求到 CDN 造成 115 严重风控告警。直接拦截不仅能防风控，还能秒开。
    if "Lavf/" in player_ua or "FFmpeg" in player_ua:
        logger.warning(f"🛡️ [{method}] 拦截播放器媒体信息提取探针 (防风控): pickcode={pickcode} ua={player_ua}")
        raise HTTPException(status_code=403, detail="Forbidden: Media probe intercepted to prevent wind control")
        
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
    
    # 如果用户没有配置伪装UA，采用最原生的 302 跳转模式
    if not target_ua:
        player_ua = request.headers.get("user-agent", "Unknown")
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
        
    logger.info(f"🔁 [{method}] Proxying {pickcode} via local stream (Spoofed UA: {target_ua})")
    
    proxy_headers = {"User-Agent": target_ua}
    if "range" in request.headers:
        proxy_headers["Range"] = request.headers["range"]
    if "if-range" in request.headers:
        proxy_headers["If-Range"] = request.headers["if-range"]

    # 极其重要：timeout=None 关闭 HTTPX 默认的 5 秒读取超时限制。
    # 否则播放器缓冲满后暂停读取 TCP，代理就会因为 5 秒没读到数据而异常断开，导致死循环报错！
    import httpx
    client = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(None))
    req = client.build_request(method, url, headers=proxy_headers)
    
    if method == "HEAD":
        try:
            resp = await client.send(req)
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]}
            await client.aclose()
            return Response(status_code=resp.status_code, headers=resp_headers)
        except Exception as e:
            logger.error(f"❌ [{method}] HEAD proxy failed: {e}")
            await client.aclose()
            raise HTTPException(status_code=502, detail="Bad Gateway")

    try:
        resp = await client.send(req, stream=True, follow_redirects=True)
    except Exception as e:
        logger.error(f"❌ [{method}] Proxy connection failed: {e}")
        await client.aclose()
        raise HTTPException(status_code=502, detail="Proxy connection failed")
        
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]}
    
    async def stream_generator():
        try:
            # 128KB 分块最适合流媒体
            async for chunk in resp.aiter_bytes(chunk_size=128 * 1024):
                yield chunk
        except Exception as e:
            logger.debug(f"⚠️ Proxy stream closed: {e}")
        finally:
            await resp.aclose()
            await client.aclose()

    from fastapi.responses import StreamingResponse
    return StreamingResponse(stream_generator(), status_code=resp.status_code, headers=resp_headers)
