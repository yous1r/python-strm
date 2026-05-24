from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
import httpx
from pydantic import BaseModel
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.cloud115.auth import auth_manager

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
async def play_video(pickcode: str, request: Request):
    """获取视频直链并通过本地代理中转视频流 (解决播放器 UA 防盗链)"""
    method = request.method
    client_ip = request.client.host if request.client else "Unknown IP"
    
    # 伪装为 iPad UA，绕过 115 CDN 风控
    ipad_ua = "Mozilla/5.0 (iPad; CPU OS 13_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.4 Mobile/15E148 Safari/604.1"
    
    # 请求 115 API 获取绑在 iPad UA 上的 CDN 直链
    url = await client_115.get_download_url(pickcode, user_agent=ipad_ua)
    if not url:
        logger.error(f"❌ [{method}] Playback failed: No URL returned for {pickcode}")
        raise HTTPException(status_code=404, detail="Download URL not found")
        
    logger.info(f"🔁 [{method}] Proxying {pickcode} for {client_ip} via local stream...")
    
    # 构造发给 115 CDN 的 Header，注入强制的 iPad UA
    proxy_headers = {"User-Agent": ipad_ua}
    if "range" in request.headers:
        proxy_headers["Range"] = request.headers["range"]
    if "if-range" in request.headers:
        proxy_headers["If-Range"] = request.headers["if-range"]

    # 启动异步流式代理
    client = httpx.AsyncClient(verify=False)
    req = client.build_request(method, url, headers=proxy_headers)
    
    # 处理 HEAD 请求
    if method == "HEAD":
        try:
            resp = await client.send(req)
            resp_headers = {}
            for k, v in resp.headers.items():
                if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]:
                    resp_headers[k] = v
            await client.aclose()
            return Response(status_code=resp.status_code, headers=resp_headers)
        except Exception as e:
            logger.error(f"❌ [{method}] HEAD proxy failed: {e}")
            await client.aclose()
            raise HTTPException(status_code=502, detail="Bad Gateway")

    # 处理 GET 请求，流式转发
    try:
        resp = await client.send(req, stream=True, follow_redirects=True)
    except Exception as e:
        logger.error(f"❌ [{method}] Proxy connection failed: {e}")
        await client.aclose()
        raise HTTPException(status_code=502, detail="Bad Gateway to 115 CDN")
    
    # 过滤可能引发播放器冲突的头
    resp_headers = {}
    for k, v in resp.headers.items():
        if k.lower() not in ["server", "date", "transfer-encoding", "content-encoding", "connection", "content-disposition"]:
            resp_headers[k] = v
            
    async def stream_generator():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):  # 1MB 块大小，优化视频缓冲
                yield chunk
        except Exception as e:
            logger.debug(f"⚠️ Proxy stream interrupted for {pickcode}: {e}")
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_generator(),
        status_code=resp.status_code,
        headers=resp_headers
    )
