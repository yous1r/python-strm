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

async def _resolve_playback_url(upstream_url: str, api_key: str, item_id: str, request: Request) -> str:
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
                    
                    player_ua = request.headers.get("user-agent", "Unknown")
                    from app.config import get_config
                    target_ua = get_config().cloud115.play_ua
                    request_ua = target_ua if target_ua else player_ua
                    
                    real_url = await client_115.get_download_url(pickcode, user_agent=request_ua)
                    if real_url:
                        logger.info(f"🔄 [STANDALONE PROXY] Redirecting /stream request directly to 115 CDN for item {item_id} (UA: {request_ua})")
                        return real_url
                        
        return None
    except Exception as e:
        logger.error(f"Failed to resolve playback url: {e}")
        return None

async def _proxy_request(upstream_url: str, api_key: str, path: str, request: Request, instance_name: str = "") -> Response:
    """透明代理请求到真实的Emby服务器，使用流式响应"""
    try:
        url = f"{upstream_url}{path}"
        params = dict(request.query_params)
        if "api_key" not in params and api_key:
            params["api_key"] = api_key
            
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'accept-encoding']}
        
        client = httpx.AsyncClient(timeout=None, follow_redirects=False)
        
        req = client.build_request(
            method=request.method,
            url=url,
            params=params,
            headers=headers,
            content=request.stream()
        )
        
        resp = await client.send(req, stream=True)
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
        
        # 重写重定向 Location，加上实例名前缀
        if 300 <= resp.status_code < 400 and instance_name:
            location = resp.headers.get("location", "")
            if location.startswith("/") and not location.startswith(f"/{instance_name}"):
                new_location = f"/{instance_name}{location}"
                resp_headers["location"] = new_location
                logger.debug(f"[PROXY] Rewrote redirect: {location} -> {new_location}")
                # 重定向无 body，直接返回
                await resp.aclose()
                await client.aclose()
                return Response(status_code=resp.status_code, headers=resp_headers)

        # 重写 HTML 中根相对路径的静态资源 URL，加上实例名前缀
        # 飞牛的 HTML 里 src="/v/assets/..." 需要变成 src="/fnos/v/assets/..."
        # <base> 标签对根相对路径无效，必须在 HTML body 中直接替换
        content_type = resp.headers.get("content-type", "")
        if instance_name and "text/html" in content_type:
            html_body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            html_text = html_body.decode("utf-8", errors="replace")
            # 匹配 src="/..." 或 href="/..." 且不包含 scheme，且不以 /{instance_name}/ 开头
            prefix = f"/{instance_name}"
            def _rewrite_path(m: re.Match) -> str:
                attr = m.group(1)
                path = m.group(2)
                if path.startswith(prefix + "/") or path.startswith(prefix + "?"):
                    return m.group(0)
                return f'{attr}="{prefix}{path}"'
            html_text = re.sub(r'(src|href)="(/[^"]*)"', _rewrite_path, html_text)
            html_text = re.sub(r"(src|href)='(/[^']*)'", _rewrite_path, html_text)
            resp_headers["content-length"] = str(len(html_text.encode("utf-8")))
            resp_headers.pop("transfer-encoding", None)
            return Response(content=html_text, media_type="text/html", status_code=resp.status_code, headers=resp_headers)
        
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

async def _intercept_playback_info(upstream_url: str, api_key: str, full_path: str, instance_name: str, request: Request) -> Response:
    """拦截 PlaybackInfo 请求，硬塞 115 直链以绕过探针"""
    # 针对 PlaybackInfo 这种小文件，直接读到内存中，因为需要修改 JSON
    url = f"{upstream_url}{full_path}"
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
                
        # 针对原生播放器，不要直接注入 115 CDN 直链（会导致 UI UA 和 播放器 UA 不一致而黑屏）
        # 而是将播放链接指向本代理的一个专属中转接口，在该接口中动态获取真正的播放器 UA
        if not is_native_player:
            logger.debug(f"⏭️ [STANDALONE PROXY] Skipped PlaybackInfo injection for Web Browser to prevent CORS infinite loop (UA: {client_ua})")
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding']}
            return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
            
        media_sources = data.get("MediaSources", [])
        
        # 构造 absolute base url，因为 IsRemote=True 时必须提供绝对地址
        # 必须考虑反向代理（如 Nginx/HTTPS）的情况，否则会导致 iOS 播放器拦截 HTTP 请求
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        base_url = f"{scheme}://{host}"
        
        for source in media_sources:
            # 必须从 Path 中提取 pickcode，因为 Emby 探针失败时会丢弃 DirectStreamUrl
            path_url = source.get("Path", "")
            if path_url and "/api/v1/115/play/" in path_url:
                match = re.search(r'/api/v1/115/play/([^/|?]+)', path_url)
                if match:
                    pickcode = match.group(1)
                    # 构造专属的 115play 中转链接绝对地址
                    proxy_play_url = f"{base_url}/{instance_name}/115play/{pickcode}"
                    source["Path"] = proxy_play_url
                    source["DirectStreamUrl"] = proxy_play_url
                    source["IsRemote"] = True
                    source["Protocol"] = "Http"
                    # 强制要求播放器进行 DirectPlay，防止 Emby 后端因探针失败而触发转码
                    source["SupportsDirectPlay"] = True
                    source["SupportsDirectStream"] = True
                    source["SupportsTranscoding"] = False
                    source["RequiresOpening"] = False
                    source["RequiresClosing"] = False
                    modified = True
                    logger.info(f"🎯 [STANDALONE PROXY] Injected proxy play URL for pickcode {pickcode} into PlaybackInfo (UA: {client_ua})")
        
        if modified:
            content = json.dumps(data).encode("utf-8")
            headers = dict(resp.headers)
            headers["content-length"] = str(len(content))
            headers.pop("content-encoding", None)
            return Response(content=content, status_code=200, headers=headers)
            
    except Exception as e:
        logger.error(f"Failed to modify PlaybackInfo JSON: {repr(e)}")
        
    # 未修改时，把 httpx Response 转为 FastAPI Response 返回
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding']}
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)

# 匹配 115play 中转请求
proxy_play_pattern = re.compile(r'/115play/([^/|?]+)', re.IGNORECASE)

@proxy_app.api_route("/{instance_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def handle_proxy_all(instance_name: str, path: str, request: Request):
    full_path = f"/{path}"
    logger.info(f"👀 [PROXY IN] {request.method} /{instance_name}{full_path}?{request.url.query} (UA: {request.headers.get('user-agent')})")
    
    config = get_config()
    
    target_instance = None
    for inst in config.emby.proxy.instances:
        if inst.name.lower() == instance_name.lower():
            target_instance = inst
            break
            
    if not target_instance:
        # 兜底：实例名不匹配时，回退到第一个配置的实例（处理 JS XHR 不带前缀的路径如 /v/api/...）
        if config.emby.proxy.instances:
            target_instance = config.emby.proxy.instances[0]
            # 把 instance_name 作为 path 的一部分拼回去
            full_path = f"/{instance_name}/{path}"
            logger.debug(f"[PROXY] Fallback: routing /{instance_name}{full_path} to default instance '{target_instance.name}'")
        else:
            return Response(status_code=404, content=f"Emby instance '{instance_name}' not found in configuration")
        
    upstream_url = target_instance.url.rstrip("/")
    api_key = target_instance.api_key
    
    # 处理专属的 115play 中转请求
    # 这里才是视频播放器真正发起请求的地方，我们可以拿到播放器真正的 UA
    play_match = proxy_play_pattern.search(full_path)
    if play_match:
        pickcode = play_match.group(1)
        player_ua = request.headers.get("user-agent", "Unknown")
        target_ua = config.cloud115.play_ua
        request_ua = target_ua if target_ua else player_ua
        
        logger.info(f"▶️ [STANDALONE PROXY] Player requested 115play for pickcode {pickcode} (Real Player UA: {player_ua})")
        
        url = await client_115.get_download_url(pickcode, user_agent=request_ua)
        if not url:
            return Response(status_code=404, content="Failed to get 115 download url")
            
        needs_m3u8 = False
        if "VidHub" in player_ua or "Infuse" in player_ua or ("Lavf/" in player_ua and "Lavf/60." not in player_ua):
            needs_m3u8 = True

        if needs_m3u8:
            logger.info(f"🎬 [STANDALONE PROXY] Serving M3U8 wrapper for pickcode {pickcode} to workaround 302 header loss")
            m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n"
            return Response(content=m3u8_content.encode("utf-8"), media_type="application/vnd.apple.mpegurl")
        else:
            logger.info(f"🔄 [STANDALONE PROXY] Redirecting to 115 CDN for pickcode {pickcode}")
            return RedirectResponse(url=url, status_code=302)
            
    if playback_info_pattern.search(full_path):
        return await _intercept_playback_info(upstream_url, api_key, full_path, instance_name, request)
        
    match = video_stream_pattern.search(full_path)
    if match:
        item_id = match.group(1)
        redirect_url = await _resolve_playback_url(upstream_url, api_key, item_id, request)
        if redirect_url:
            return RedirectResponse(url=redirect_url, status_code=302)
            
    return await _proxy_request(upstream_url, api_key, full_path, request, instance_name)

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
