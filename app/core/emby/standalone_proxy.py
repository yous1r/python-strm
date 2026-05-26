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
    """解析出真实播放地址，用于支持 Web 浏览器的 Direct Stream 跳转"""
    try:
        # 如果没有配置 API Key，我们无法请求 Items 接口。
        # 此时无法进行 stream 请求拦截替换，只能放弃
        if not api_key:
            return None
            
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{upstream_url}/emby/Items/{item_id}",
                params={"api_key": api_key}
            )
            if res.status_code != 200:
                return None
                
            item_data = res.json()
            path = item_data.get("Path", "")
            
            if path and "/api/v1/115/play/" in path:
                match = re.search(r'/api/v1/115/play/([^/|?]+)', path)
                if match:
                    pickcode = match.group(1)
                    # For Web browser proxying, use a standard browser UA
                    real_url = await client_115.get_download_url(pickcode, user_agent="Mozilla/5.0")
                    if real_url:
                        logger.info(f"🔄 [STANDALONE PROXY] Redirecting /stream request directly to 115 CDN for item {item_id}")
                        return real_url
                        
        return None
    except Exception as e:
        logger.error(f"Failed to resolve playback url: {e}")
        return None

async def _proxy_request(upstream_url: str, api_key: str, path: str, request: Request) -> Response:
    """透明代理请求到真实的Emby服务器，使用流式响应"""
    try:
        url = f"{upstream_url}{path}"
        params = dict(request.query_params)
        if "api_key" not in params and api_key:
            params["api_key"] = api_key
            
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'accept-encoding']}
        
        client = httpx.AsyncClient(timeout=None)  # 禁用超时，避免大文件或视频流断开
        
        req = client.build_request(
            method=request.method,
            url=url,
            params=params,
            headers=headers,
            content=request.stream()
        )
        
        resp = await client.send(req, stream=True)
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
        
        async def stream_generator():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except Exception as e:
                # 客户端断开连接是正常现象，忽略错误
                pass
            finally:
                await resp.aclose()
                await client.aclose()
                
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            stream_generator(),
            status_code=resp.status_code,
            headers=resp_headers
        )
    except Exception as e:
        logger.error(f"Proxy request failed to {upstream_url}{path}: {repr(e)}")
        return Response(status_code=502, content="Bad Gateway")

async def _intercept_playback_info(upstream_url: str, api_key: str, path: str, request: Request) -> Response:
    """拦截 PlaybackInfo 请求，硬塞 115 直链以绕过探针"""
    # 针对 PlaybackInfo 这种小文件，直接读到内存中，因为需要修改 JSON
    url = f"{upstream_url}{path}"
    params = dict(request.query_params)
    if "api_key" not in params and api_key:
        params["api_key"] = api_key
        
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'accept-encoding']}
    body = await request.body()
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                params=params,
                headers=headers,
                content=body
            )
            
            if resp.status_code != 200:
                resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
                return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
                
            data = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch PlaybackInfo from {url}: {repr(e)}")
        return Response(status_code=502, content="Bad Gateway")
        
    try:
        modified = False
        
        client_ua = request.headers.get("user-agent", "Unknown")
        
        # 核心防循环逻辑：
        # Web浏览器 (Chrome/Edge/Safari) 无法跨域播放 115 CDN (无CORS头)。
        # 如果强制注入直链，浏览器会播放失败并陷入 PlaybackInfo 疯狂重试的死循环！
        # 因此，只有原生播放器 (VidHub, Infuse, AppleTV, 客户端等) 才进行劫持注入。
        is_native_player = False
        ua_lower = client_ua.lower()
        native_keywords = ["vidhub", "infuse", "applecoremedia", "vlc", "potplayer", "iina", "kodi", "lavf", "mpv", "xbmc", "embyclient"]
        for kw in native_keywords:
            if kw in ua_lower:
                is_native_player = True
                break
                
        if not is_native_player:
            logger.debug(f"⏭️ [STANDALONE PROXY] Skipped PlaybackInfo injection for Web Browser to prevent CORS infinite loop (UA: {client_ua})")
            return resp
            
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

_proxy_task = None

async def start_standalone_proxy():
    global _proxy_server_instance, _proxy_task
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
    finally:
        _proxy_server_instance = None

async def stop_standalone_proxy():
    global _proxy_server_instance, _proxy_task
    if _proxy_server_instance:
        logger.info("Stopping Standalone Emby Reverse Proxy...")
        _proxy_server_instance.should_exit = True
        # wait a bit for shutdown
        await asyncio.sleep(0.5)

async def restart_standalone_proxy():
    """重启或根据配置启动/停止代理服务 (热重载)"""
    global _proxy_task
    config = get_config()
    
    await stop_standalone_proxy()
    
    if _proxy_task and not _proxy_task.done():
        _proxy_task.cancel()
        
    if config.emby.proxy.enabled:
        logger.info("Hot reloading Standalone Proxy...")
        _proxy_task = asyncio.create_task(start_standalone_proxy())
