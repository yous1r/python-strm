import asyncio
import httpx
import re
import json
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from loguru import logger
import uvicorn

from app.config import get_config
from app.core.cloud115.client import client_115

proxy_app = FastAPI(title="Python-STRM Emby Proxy", docs_url=None, redoc_url=None)

# 匹配视频流请求
video_stream_pattern = re.compile(r'/videos/(\w+)/stream', re.IGNORECASE)
# 匹配 PlaybackInfo 请求
playback_info_pattern = re.compile(r'/Items/(\w+)/PlaybackInfo', re.IGNORECASE)

async def _resolve_playback_url(upstream_url: str, api_key: str, item_id: str) -> str:
    """解析出真实播放地址"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{upstream_url}/emby/Items/{item_id}",
                params={"api_key": api_key}
            )
            if res.status_code != 200:
                return None
                
            item_data = res.json()
            path = item_data.get("Path", "")
            
            if path.endswith(".strm"):
                # 如果未来需要处理 stream 请求的 302 跳转
                pass
                
        return None
    except Exception as e:
        logger.error(f"Failed to resolve playback url: {e}")
        return None

async def _proxy_request(upstream_url: str, api_key: str, path: str, request: Request) -> Response:
    """透明代理请求到真实的Emby服务器"""
    try:
        url = f"{upstream_url}{path}"
        params = dict(request.query_params)
        if "api_key" not in params and api_key:
            params["api_key"] = api_key
            
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'accept-encoding']}
        
        body = await request.body()
        
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method=request.method,
                url=url,
                params=params,
                headers=headers,
                content=body,
                timeout=30.0
            )
            
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
            
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers
            )
    except Exception as e:
        logger.error(f"Proxy request failed: {e}")
        return Response(status_code=502, content="Bad Gateway")

async def _intercept_playback_info(upstream_url: str, api_key: str, path: str, request: Request) -> Response:
    """拦截 PlaybackInfo 请求，硬塞 115 直链以绕过探针"""
    resp = await _proxy_request(upstream_url, api_key, path, request)
    if resp.status_code != 200:
        return resp
        
    try:
        data = json.loads(resp.body)
        modified = False
        
        client_ua = request.headers.get("user-agent", "Unknown")
        media_sources = data.get("MediaSources", [])
        
        for source in media_sources:
            for key in ["Path", "DirectStreamUrl"]:
                url = source.get(key, "")
                if url and "/api/v1/115/play/" in url:
                    match = re.search(r'/api/v1/115/play/([^/|?]+)', url)
                    if match:
                        pickcode = match.group(1)
                        real_url = await client_115.get_download_url(pickcode, user_agent=client_ua)
                        if real_url:
                            source[key] = real_url
                            source["IsRemote"] = True
                            source["Protocol"] = "Http"
                            modified = True
                            logger.info(f"🎯 [STANDALONE PROXY] Injected 115 CDN URL for pickcode {pickcode} into PlaybackInfo (UA: {client_ua})")
        
        if modified:
            content = json.dumps(data).encode("utf-8")
            headers = dict(resp.headers)
            headers["content-length"] = str(len(content))
            headers.pop("content-encoding", None)
            return Response(content=content, status_code=200, headers=headers)
            
    except Exception as e:
        logger.error(f"Failed to modify PlaybackInfo JSON: {e}")
        
    return resp

@proxy_app.api_route("/{instance_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def handle_proxy_all(instance_name: str, path: str, request: Request):
    config = get_config()
    
    target_instance = None
    for inst in config.emby.proxy.instances:
        if inst.name.lower() == instance_name.lower():
            target_instance = inst
            break
            
    if not target_instance:
        return Response(status_code=404, content=f"Emby instance '{instance_name}' not found in configuration")
        
    upstream_url = target_instance.url.rstrip("/")
    api_key = target_instance.api_key
    
    full_path = f"/{path}"
    
    if playback_info_pattern.search(full_path):
        return await _intercept_playback_info(upstream_url, api_key, full_path, request)
        
    match = video_stream_pattern.search(full_path)
    if match:
        item_id = match.group(1)
        redirect_url = await _resolve_playback_url(upstream_url, api_key, item_id)
        if redirect_url:
            return RedirectResponse(url=redirect_url, status_code=302)
            
    return await _proxy_request(upstream_url, api_key, full_path, request)

@proxy_app.api_route("/{instance_name}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def handle_proxy_root(instance_name: str, request: Request):
    return await handle_proxy_all(instance_name, "", request)

_proxy_server_instance = None

async def start_standalone_proxy():
    global _proxy_server_instance
    config = get_config()
    if not config.emby.proxy.enabled:
        return
        
    port = config.emby.proxy.port
    logger.info(f"Starting Standalone Emby Reverse Proxy on port {port}...")
    
    uvicorn_config = uvicorn.Config(app=proxy_app, host="0.0.0.0", port=port, log_level="error")
    _proxy_server_instance = uvicorn.Server(uvicorn_config)
    
    try:
        await _proxy_server_instance.serve()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Standalone Proxy server error: {e}")

async def stop_standalone_proxy():
    global _proxy_server_instance
    if _proxy_server_instance:
        logger.info("Stopping Standalone Emby Reverse Proxy...")
        _proxy_server_instance.should_exit = True
