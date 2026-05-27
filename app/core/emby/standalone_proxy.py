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

# 匹配视频流请求
video_stream_pattern = re.compile(r'/videos/(\w+)/stream', re.IGNORECASE)
# 匹配 PlaybackInfo 请求
playback_info_pattern = re.compile(r'/Items/(\w+)/PlaybackInfo', re.IGNORECASE)
# 匹配 115play 中转请求
proxy_play_pattern = re.compile(r'/115play/([^/|?]+)', re.IGNORECASE)


async def _resolve_playback_url(upstream_url: str, api_key: str, item_id: str, request: Request) -> str:
    """解析出真实播放地址"""
    try:
        if not api_key:
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{upstream_url}/v/emby/Items/{item_id}",
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
                    config = get_config()
                    target_ua = config.cloud115.play_ua
                    request_ua = target_ua if target_ua else player_ua

                    real_url = await client_115.get_download_url(pickcode, user_agent=request_ua)
                    if real_url:
                        return real_url

        return None
    except Exception as e:
        logger.error(f"Failed to resolve playback url: {e}")
        return None


async def _proxy_request(upstream_url: str, api_key: str, full_path: str, request: Request) -> Response:
    """透明代理请求到真实的Emby服务器"""
    try:
        url = f"{upstream_url}{full_path}"
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

        # 3xx 重定向：透传 Location（端口代理不需要改写前缀）
        if 300 <= resp.status_code < 400:
            await resp.aclose()
            await client.aclose()
            return Response(status_code=resp.status_code, headers=resp_headers)

        async def stream_generator():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except Exception:
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
        logger.error(f"Proxy request failed to {upstream_url}{full_path}: {repr(e)}")
        return Response(status_code=502, content="Bad Gateway")


async def _intercept_playback_info(upstream_url: str, api_key: str, full_path: str, request: Request) -> Response:
    """拦截 PlaybackInfo 请求，注入代理播放 URL 绕过探针"""
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

        # 仅原生播放器进行劫持注入，Web 浏览器跳过（防 CORS 死循环）
        is_native_player = False
        ua_lower = client_ua.lower()
        native_keywords = ["vidhub", "infuse", "applecoremedia", "vlc", "potplayer", "iina", "kodi", "lavf", "mpv", "xbmc", "embyclient"]
        for kw in native_keywords:
            if kw in ua_lower:
                is_native_player = True
                break

        if not is_native_player:
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding']}
            return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)

        media_sources = data.get("MediaSources", [])

        # 构造 absolute base url（host 含端口，如 192.168.1.100:8096）
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        base_url = f"{scheme}://{host}"

        for source in media_sources:
            path_url = source.get("Path", "")
            if path_url and "/api/v1/115/play/" in path_url:
                match = re.search(r'/api/v1/115/play/([^/|?]+)', path_url)
                if match:
                    pickcode = match.group(1)
                    proxy_play_url = f"{base_url}/115play/{pickcode}"
                    source["Path"] = proxy_play_url
                    source["DirectStreamUrl"] = proxy_play_url
                    source["IsRemote"] = True
                    source["Protocol"] = "Http"
                    source["SupportsDirectPlay"] = True
                    source["SupportsDirectStream"] = True
                    source["SupportsTranscoding"] = False
                    source["RequiresOpening"] = False
                    source["RequiresClosing"] = False
                    modified = True
                    logger.info(f"[PROXY] Injected proxy play URL for pickcode {pickcode} (UA: {client_ua})")

        if modified:
            content = json.dumps(data).encode("utf-8")
            headers = dict(resp.headers)
            headers["content-length"] = str(len(content))
            headers.pop("content-encoding", None)
            return Response(content=content, status_code=200, headers=headers)

    except Exception as e:
        logger.error(f"Failed to modify PlaybackInfo JSON: {repr(e)}")

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding']}
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)


def create_proxy_app(instance) -> FastAPI:
    """为单个 Emby 实例创建专属的反向代理 FastAPI 应用"""
    upstream_url = instance.url.rstrip("/")
    api_key = instance.api_key

    app = FastAPI(title=f"Python-STRM Proxy - {instance.name}", docs_url=None, redoc_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    async def handle_proxy(path: str, request: Request):
        full_path = f"/{path}"
        logger.info(f"[PROXY] {request.method} /{path}{f'?{request.url.query}' if request.url.query else ''} (UA: {request.headers.get('user-agent', 'Unknown')[:60]})")
        config = get_config()

        # 115play 中转：播放器真正请求时拿到真实 UA，动态取 CDN 链
        play_match = proxy_play_pattern.search(full_path)
        if play_match:
            pickcode = play_match.group(1)
            player_ua = request.headers.get("user-agent", "Unknown")
            target_ua = config.cloud115.play_ua
            request_ua = target_ua if target_ua else player_ua

            logger.info(f"[PROXY] Player requested 115play for pickcode {pickcode} (UA: {player_ua})")

            url = await client_115.get_download_url(pickcode, user_agent=request_ua)
            if not url:
                return Response(status_code=404, content="Failed to get 115 download url")

            needs_m3u8 = False
            if "VidHub" in player_ua or "Infuse" in player_ua or ("Lavf/" in player_ua and "Lavf/60." not in player_ua):
                needs_m3u8 = True

            if needs_m3u8:
                m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n"
                return Response(content=m3u8_content.encode("utf-8"), media_type="application/vnd.apple.mpegurl")
            else:
                return RedirectResponse(url=url, status_code=302)

        # Feiniu /v/ 路径前缀：VidHub 发 /emby/... 时自动补全为 /v/emby/...
        # 浏览器发 /v/emby/... 时已含前缀，不做改动
        if not full_path.startswith("/v/") and full_path != "/v":
            full_path = f"/v{full_path}"

        # 拦截 PlaybackInfo
        if playback_info_pattern.search(full_path):
            return await _intercept_playback_info(upstream_url, api_key, full_path, request)

        # 拦截视频流请求
        match = video_stream_pattern.search(full_path)
        if match:
            item_id = match.group(1)
            redirect_url = await _resolve_playback_url(upstream_url, api_key, item_id, request)
            if redirect_url:
                return RedirectResponse(url=redirect_url, status_code=302)

        return await _proxy_request(upstream_url, api_key, full_path, request)

    @app.api_route("/", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    async def handle_root(request: Request):
        return await handle_proxy("", request)

    return app


# ── 生命周期管理 ──────────────────────────────────────────────

_proxy_servers: list[uvicorn.Server] = []
_proxy_task = None


async def start_standalone_proxy():
    global _proxy_servers
    config = get_config()
    if not config.emby.proxy.enabled:
        return

    for instance in config.emby.proxy.instances:
        if not instance.proxy_port:
            logger.warning(f"[PROXY] Instance '{instance.name}' has no proxy_port configured, skipping")
            continue

        proxy_app = create_proxy_app(instance)
        uvicorn_config = uvicorn.Config(app=proxy_app, host="0.0.0.0", port=instance.proxy_port, log_level="debug")
        server = uvicorn.Server(uvicorn_config)
        _proxy_servers.append(server)

        logger.info(f"[PROXY] Starting for '{instance.name}' on port {instance.proxy_port} -> {instance.url}")
        asyncio.create_task(_serve_proxy(server, instance.name))

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass


async def _serve_proxy(server: uvicorn.Server, name: str):
    try:
        await server.serve()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[PROXY] Server error for '{name}': {e}")


async def stop_standalone_proxy():
    global _proxy_servers
    for server in _proxy_servers:
        if server.started:
            server.should_exit = True
    _proxy_servers.clear()
    await asyncio.sleep(0.5)


async def restart_standalone_proxy():
    global _proxy_task

    await stop_standalone_proxy()

    if _proxy_task and not _proxy_task.done():
        _proxy_task.cancel()

    config = get_config()
    if config.emby.proxy.enabled:
        logger.info("[PROXY] Hot reloading Standalone Proxy...")
        _proxy_task = asyncio.create_task(start_standalone_proxy())