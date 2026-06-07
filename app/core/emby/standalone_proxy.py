import asyncio
import httpx
import re
from app.events import spawn_task
import json
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import RedirectResponse
from loguru import logger
import uvicorn

from app.config import get_config
from app.database import get_db_conn
from app.core.cloud115.client import client_115

# 匹配视频流请求
video_stream_pattern = re.compile(r'/videos/(\w+)/stream', re.IGNORECASE)
# 匹配 PlaybackInfo 请求
playback_info_pattern = re.compile(r'/Items/(\w+)/PlaybackInfo', re.IGNORECASE)
# 匹配 115play 中转请求
proxy_play_pattern = re.compile(r'/115play/([^/|?]+)', re.IGNORECASE)


def _normalize_media_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def _join_mapped_path(local_prefix: str, relative_path: str) -> str:
    import os

    local_prefix = os.path.normpath(local_prefix)
    relative_path = relative_path.lstrip("/")
    if not relative_path:
        return local_prefix
    return os.path.normpath(os.path.join(local_prefix, *relative_path.split("/")))


def _build_upstream_url(upstream_url: str, path: str) -> str:
    """统一拼接 Emby 上游 URL，避免重复追加 /emby 前缀。"""
    base = upstream_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"

    if base.endswith("/emby") and normalized_path.startswith("/emby/"):
        normalized_path = normalized_path[len("/emby"):]
    elif not base.endswith("/emby") and not normalized_path.startswith("/emby/"):
        normalized_path = f"/emby{normalized_path}"

    return f"{base}{normalized_path}"


def _resolve_local_strm_path(emby_path: str, instance=None) -> str | None:
    """将 Emby 媒体库中的 STRM 路径映射为代理本地可读路径"""
    import os

    normalized_path = _normalize_media_path(emby_path)

    mappings = []
    if instance is not None:
        mappings = getattr(instance, "strm_path_mappings", []) or []

    sorted_mappings = sorted(
        mappings,
        key=lambda item: len(_normalize_media_path(getattr(item, "emby_prefix", ""))),
        reverse=True,
    )
    for mapping in sorted_mappings:
        emby_prefix = _normalize_media_path(getattr(mapping, "emby_prefix", ""))
        local_prefix = getattr(mapping, "local_prefix", "")
        if not emby_prefix or not local_prefix:
            continue
        if normalized_path == emby_prefix or normalized_path.startswith(f"{emby_prefix}/"):
            relative = normalized_path[len(emby_prefix):].lstrip("/")
            candidate = _join_mapped_path(local_prefix, relative)
            if os.path.exists(candidate):
                return candidate
            logger.warning(f"[PROXY] STRM path mapping matched but local file does not exist: {emby_path} -> {candidate}")

    # 兼容旧逻辑：飞牛路径 /vol1/docker-data/python-strm/strm_output/... 映射到本项目输出目录
    for marker in ["python-strm/strm_output/", "strm_output/"]:
        idx = normalized_path.find(marker)
        if idx >= 0:
            relative = normalized_path[idx + len(marker):]
            candidates = [
                os.path.join("strm_output", relative),
                os.path.join("/app/strm_output", relative),
            ]
            for c in candidates:
                if os.path.exists(c):
                    return c
    return None


def _get_emby_headers(request: Request, configured_key: str = "") -> dict:
    """根据客户端请求构造统一的 Emby/飞牛 兼容鉴权请求头"""
    headers = {
        "Accept": "application/json",
    }
    # 1. 提取 Token
    token = request.headers.get("x-emby-token")
    if not token:
        token = request.query_params.get("api_key")
    if not token:
        auth_header = request.headers.get("x-emby-authorization", "")
        match = re.search(r'Token="([^"]+)"', auth_header, re.IGNORECASE)
        if match:
            token = match.group(1)
            
    effective_token = token if token else configured_key
    if effective_token:
        headers["X-Emby-Token"] = effective_token
        
    # 2. 提取并透传完整 X-Emby-Authorization
    auth_val = request.headers.get("x-emby-authorization")
    if auth_val:
        headers["X-Emby-Authorization"] = auth_val
        
    return headers


def _get_emby_user_id(request: Request) -> str | None:
    """从 query 或 X-Emby-Authorization 中提取 UserId。"""
    user_id = request.query_params.get("UserId") or request.query_params.get("userId")
    if user_id:
        return user_id

    auth_header = request.headers.get("x-emby-authorization", "")
    match = re.search(r'UserId="([^"]+)"', auth_header, re.IGNORECASE)
    return match.group(1) if match else None


async def _request_upstream_json(
    upstream_url: str,
    path: str,
    request: Request,
    api_key: str = "",
    *,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 10,
) -> httpx.Response | None:
    """统一发送到上游 Emby 的 JSON 请求。"""
    headers = _get_emby_headers(request, api_key)
    if "X-Emby-Token" not in headers:
        logger.warning(f"[PROXY] Missing Emby token while requesting upstream path={path}")
        return None

    url = _build_upstream_url(upstream_url, path)
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            return await client.request(method=method, url=url, params=params, json=json_body)
    except Exception as e:
        logger.error(f"[PROXY] Upstream JSON request failed for {method} {url}: {repr(e)}")
        return None


async def _build_upstream_proxy_request(
    upstream_url: str,
    full_path: str,
    request: Request,
    api_key: str = "",
) -> tuple[httpx.AsyncClient, httpx.Response]:
    """统一构建并发送透明代理请求到上游 Emby。"""
    url = _build_upstream_url(upstream_url, full_path)
    params = dict(request.query_params)
    if "api_key" not in params and api_key:
        params["api_key"] = api_key

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "accept-encoding"]}
    logger.debug(f"[PROXY] Forwarding request to {url} with params={params}, headers={headers}")

    client = httpx.AsyncClient(timeout=None, follow_redirects=False)
    req_content = await request.body() if request.method in ("POST", "PUT", "PATCH") else request.stream()
    req = client.build_request(
        method=request.method,
        url=url,
        params=params,
        headers=headers,
        content=req_content,
    )
    resp = await client.send(req, stream=True)
    return client, resp


async def _get_upstream_item_payload(upstream_url: str, api_key: str, item_id: str, request: Request) -> dict | None:
    """读取上游 Emby/FNOS 的 Item 信息，用于把 ItemId 映射回 STRM 路径。"""
    user_id = _get_emby_user_id(request)
    api_path = f"/Users/{user_id}/Items/{item_id}" if user_id else f"/Items/{item_id}"

    res = await _request_upstream_json(
        upstream_url,
        api_path,
        request,
        api_key,
        params={"Fields": "Path,MediaSources"},
    )
    if res is None:
        return None
    if res.status_code != 200:
        logger.warning(f"[PROXY] Upstream item lookup failed for item_id={item_id}, status={res.status_code}")
        return None

    try:
        return res.json()
    except Exception as e:
        logger.error(f"[PROXY] Failed to decode upstream item payload for item_id={item_id}: {repr(e)}")
        return None


async def _get_local_playback_record_by_path(feiniu_path: str, instance=None) -> dict | None:
    """通过上游返回的 STRM 路径反查本地 manifest 记录。"""
    local_path = _resolve_local_strm_path(feiniu_path, instance)
    if not local_path:
        logger.warning(f"[PROXY] Could not map upstream path to local STRM path: {feiniu_path}")
        return None

    normalized_local_path = local_path.replace("\\", "/")
    rel_path = normalized_local_path
    marker = "strm_output/"
    idx = normalized_local_path.find(marker)
    if idx >= 0:
        rel_path = normalized_local_path[idx + len(marker):]

    try:
        async with get_db_conn() as db:
            cursor = await db.execute(
                '''
                SELECT file_id, play_identity, strm_rel_path, strm_abs_path, strm_path
                FROM strm_records
                WHERE cloud_type='115'
                  AND (
                    strm_abs_path=?
                    OR strm_path=?
                    OR strm_rel_path=?
                  )
                LIMIT 1
                ''',
                (local_path, local_path, rel_path),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[PROXY] Failed to load local playback record by path={local_path}: {repr(e)}")
        return None


async def _resolve_playback_url(upstream_url: str, api_key: str, item_id: str, request: Request) -> str:
    """解析出真实播放地址"""
    logger.debug(f"[PROXY] Resolving playback URL for item_id={item_id}")
    try:
        item_data = await _get_upstream_item_payload(upstream_url, api_key, item_id, request)
        if item_data:
            path = item_data.get("Path", "")
            if not path and item_data.get("MediaSources"):
                path = item_data["MediaSources"][0].get("Path", "")

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
        url = _build_upstream_url(upstream_url, full_path)
        client, resp = await _build_upstream_proxy_request(upstream_url, full_path, request, api_key)
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
        logger.debug(f"[PROXY] Received upstream response from {url}: status={resp.status_code}")

        # 拦截 PlaySession 错误：如果因为虚假 Session ID 导致飞牛报错，直接返回 204，防止播放器崩溃
        if resp.status_code >= 400 and "/Sessions/Playing" in url:
            logger.warning(f"[PROXY] Upstream Emby rejected {url} with {resp.status_code}, rewriting to 204 No Content to prevent client crash.")
            await resp.aclose()
            await client.aclose()
            return Response(status_code=204, headers={})

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
        logger.error(f"Proxy request failed to {_build_upstream_url(upstream_url, full_path)}: {repr(e)}")
        return Response(status_code=502, content="Bad Gateway")


async def _intercept_playback_info(upstream_url: str, api_key: str, full_path: str, request: Request, instance=None) -> Response:
    """本地合成 PlaybackInfo，避免再向 Emby 请求探测信息。"""
    logger.info(f"[PROXY] Intercepting PlaybackInfo locally for {full_path}")
    body = await request.body()
    logger.debug(f"[PROXY] PlaybackInfo request payload ({len(body)} bytes): {body[:2000].decode('utf-8', errors='replace') if body else '(empty)'}")

    match = playback_info_pattern.search(full_path)
    if not match:
        return Response(status_code=400, content="Invalid PlaybackInfo path")

    item_id = match.group(1)
    record = None
    item_data = await _get_upstream_item_payload(upstream_url, api_key, item_id, request)
    path = ""
    if item_data:
        path = item_data.get("Path", "")
        if not path and item_data.get("MediaSources"):
            path = item_data["MediaSources"][0].get("Path", "")

    if path:
        if "/api/v1/115/play/" in path:
            direct_match = re.search(r'/api/v1/115/play/([^/|?]+)', path)
            if direct_match:
                record = {
                    "file_id": None,
                    "play_identity": direct_match.group(1),
                    "strm_rel_path": None,
                    "strm_abs_path": None,
                    "strm_path": path,
                }
        else:
            record = await _get_local_playback_record_by_path(path, instance)

    if not record:
        logger.warning(f"[PROXY] No playback mapping found for item_id={item_id}")
        return Response(status_code=404, content="Playback source not found")

    pickcode = record.get("play_identity") or ""
    if not pickcode:
        logger.warning(f"[PROXY] Local STRM manifest record missing play_identity for item_id={item_id}")
        return Response(status_code=404, content="Playback identity not found")

    media_source_id = request.query_params.get("MediaSourceId") or item_id

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    base_url = f"{scheme}://{host}"
    proxy_play_url = f"{base_url}/115play/{pickcode}"

    media_source = {
        "Id": media_source_id,
        "Name": "115 Cloud Video",
        "Path": proxy_play_url,
        "DirectStreamUrl": proxy_play_url,
        "Protocol": "Http",
        "Type": "Default",
        "Container": "mkv",
        "IsRemote": True,
        "ReadAtNativeFramerate": False,
        "SupportsDirectPlay": True,
        "SupportsDirectStream": True,
        "SupportsTranscoding": False,
        "RequiresOpening": False,
        "RequiresClosing": False,
        "MediaStreams": [],
        "Formats": [],
        "Bitrate": 0,
        "RequiredHttpHeaders": {},
    }

    strm_rel_path = record.get("strm_rel_path") or record.get("strm_path") or record.get("strm_abs_path") or ""
    if strm_rel_path:
        media_source["ItemId"] = item_id
        media_source["FileName"] = strm_rel_path.rsplit("/", 1)[-1]

    data = {
        "MediaSources": [media_source],
        "PlaySessionId": request.query_params.get("PlaySessionId") or item_id,
    }

    content = json.dumps(data).encode("utf-8")
    logger.info(f"[PROXY] Built local PlaybackInfo for item_id={item_id}, pickcode={pickcode}")
    return Response(content=content, status_code=200, media_type="application/json")


def create_proxy_app(instance) -> FastAPI:
    """为单个 Emby 实例创建专属的反向代理 FastAPI 应用"""
    upstream_url = instance.url.rstrip("/")
    api_key = instance.api_key

    app = FastAPI(title=f"Python-STRM Proxy - {instance.name}", docs_url=None, redoc_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    async def handle_proxy(path: str, request: Request, background_tasks: BackgroundTasks):
        full_path = f"/{path}"
        logger.info(f"[PROXY] {request.method} /{path}{f'?{request.url.query}' if request.url.query else ''} (UA: {request.headers.get('user-agent', 'Unknown')})")
        logger.debug(f"[PROXY] handle_proxy headers: {dict(request.headers)}")
        if request.method in ('POST', 'PUT', 'PATCH'):
            try:
                body_bytes = await request.body()
                logger.debug(f"[PROXY] handle_proxy request payload ({len(body_bytes)} bytes): {body_bytes[:2000].decode('utf-8', errors='replace') if body_bytes else '(empty)'}")

                # 拦截所有播放会话事件（开始/进度/停止），修正 RunTimeTicks 并强行同步进度
                if request.method == "POST" and "/Sessions/Playing" in full_path:
                    try:
                        if body_bytes:
                            payload = json.loads(body_bytes)
                            item_id = payload.get("ItemId")
                            position_ticks = payload.get("PositionTicks")
                            runtime_ticks = payload.get("RunTimeTicks")
                            
                            user_id = _get_emby_user_id(request)
                            
                            is_start = full_path.rstrip("/").endswith("/Sessions/Playing") and "/Progress" not in full_path and "/Stopped" not in full_path
                            is_progress = "/Sessions/Playing/Progress" in full_path
                            is_stopped = "/Sessions/Playing/Stopped" in full_path
                            event_type = "Start" if is_start else ("Progress" if is_progress else ("Stopped" if is_stopped else "Unknown"))
                            
                            # 缓存/获取真实视频时长
                            if item_id and runtime_ticks:
                                _item_runtimes[item_id] = runtime_ticks
                            effective_runtime_ticks = runtime_ticks if runtime_ticks else _item_runtimes.get(item_id)
                            
                            # 提取本次请求对应的可用 Headers
                            effective_headers = _get_emby_headers(request, api_key)
                            has_token = "X-Emby-Token" in effective_headers
                            has_config = bool(api_key)
                            
                            logger.info(
                                f"[PROXY] 🎬 Intercepted Sessions/Playing event: {event_type}, "
                                f"ItemId={item_id}, UserId={user_id}, PositionTicks={position_ticks}, "
                                f"RunTimeTicks={effective_runtime_ticks}, api_key={'CONFIGURED' if has_config else ('CLIENT_TOKEN' if has_token else 'EMPTY')}"
                            )
                            
                            if item_id and user_id:
                                async def fix_runtime_and_sync(u_id, i_id, pos_ticks, rt_ticks, evt_type, _upstream_url, _request, _api_key):
                                    """修正 RunTimeTicks 并同步播放进度"""
                                    return
                                    logger.info(f"[PROXY] 🔧 Background task started for {i_id} (Event: {evt_type}, rt_ticks={rt_ticks}, pos_ticks={pos_ticks})")
                                    
                                    if evt_type == "Stopped":
                                        # 延迟 1.5 秒以确保飞牛处理完 Stopped 接口的清零操作后再强制写回进度
                                        await asyncio.sleep(1.5)
                                        
                                    if "X-Emby-Token" not in _get_emby_headers(_request, _api_key):
                                        logger.error(f"[PROXY] ❌ Both configured api_key and client token are empty! Cannot call Emby API for {i_id}")
                                        return
                                    
                                    try:
                                        # ── 步骤 1：修正 RunTimeTicks ──
                                        if rt_ticks and rt_ticks > 10_000_000:
                                            try:
                                                item_path = f"/Items/{i_id}"
                                                logger.debug(f"[PROXY] GET {item_path}...")
                                                item_resp = await _request_upstream_json(_upstream_url, item_path, _request, _api_key)
                                                if item_resp is None:
                                                    logger.error(f"[PROXY] ❌ GET Items failed: missing token or upstream request error")
                                                    return

                                                logger.info(f"[PROXY] GET Items/{i_id} status={item_resp.status_code}")
                                                if item_resp.status_code == 200:
                                                    item_dto = item_resp.json()
                                                    db_runtime = item_dto.get("RunTimeTicks", 0) or 0
                                                    logger.info(f"[PROXY] DB RunTimeTicks={db_runtime} ({db_runtime/10_000_000:.1f}s), Real={rt_ticks} ({rt_ticks/10_000_000:.1f}s)")

                                                    if abs(db_runtime - rt_ticks) > 10_000_000:
                                                        item_dto["RunTimeTicks"] = rt_ticks
                                                        update_resp = await _request_upstream_json(
                                                            _upstream_url,
                                                            item_path,
                                                            _request,
                                                            _api_key,
                                                            method="POST",
                                                            json_body=item_dto,
                                                        )
                                                        if update_resp is not None and update_resp.status_code < 400:
                                                            logger.info(f"[PROXY] ✅ Fixed RunTimeTicks for {i_id}: {db_runtime} → {rt_ticks}")
                                                        elif update_resp is not None:
                                                            logger.error(f"[PROXY] ❌ Failed to fix RunTimeTicks: status={update_resp.status_code}, body={update_resp.text[:500]}")
                                                        else:
                                                            logger.error(f"[PROXY] ❌ Failed to fix RunTimeTicks: missing token or upstream request error")
                                                    else:
                                                        logger.info(f"[PROXY] RunTimeTicks already correct for {i_id}, skip")
                                                else:
                                                    logger.error(f"[PROXY] ❌ GET Items failed: status={item_resp.status_code}, body={item_resp.text[:500]}")
                                            except Exception as e:
                                                logger.error(f"[PROXY] RunTimeTicks fix error for {i_id}: {repr(e)}")
                                        else:
                                            logger.debug(f"[PROXY] No RunTimeTicks in payload or too small (rt_ticks={rt_ticks}), skip fix")

                                        # ── 步骤 2：同步播放进度到 UserData（永久落盘） ──
                                        if pos_ticks is not None and pos_ticks >= 0:
                                            try:
                                                from datetime import datetime, timezone, timedelta
                                                beijing_tz = timezone(timedelta(hours=8))

                                                user_item_path = f"/Users/{u_id}/Items/{i_id}"
                                                get_resp = await _request_upstream_json(_upstream_url, user_item_path, _request, _api_key)
                                                if get_resp is None:
                                                    logger.error(f"[PROXY] ❌ GET UserItem failed: missing token or upstream request error")
                                                    return

                                                logger.debug(f"[PROXY] GET UserItem status={get_resp.status_code}")
                                                if get_resp.status_code == 200:
                                                    user_item = get_resp.json()
                                                    user_data = user_item.get("UserData", {})

                                                    user_data["PlaybackPositionTicks"] = pos_ticks
                                                    user_data["Played"] = False
                                                    user_data["LastPlayedDate"] = datetime.now(beijing_tz).isoformat()

                                                    post_resp = await _request_upstream_json(
                                                        _upstream_url,
                                                        f"/Users/{u_id}/Items/{i_id}/UserData",
                                                        _request,
                                                        _api_key,
                                                        method="POST",
                                                        json_body=user_data,
                                                    )
                                                    if post_resp is not None and post_resp.status_code < 400:
                                                        logger.info(f"[PROXY] ✅ Synced UserData for {i_id} (Ticks: {pos_ticks}, Event: {evt_type})")
                                                    elif post_resp is not None:
                                                        logger.error(f"[PROXY] ❌ UserData sync failed: status={post_resp.status_code}, body={post_resp.text[:500]}")
                                                    else:
                                                        logger.error(f"[PROXY] ❌ UserData sync failed: missing token or upstream request error")
                                                else:
                                                    logger.error(f"[PROXY] ❌ GET UserItem failed: status={get_resp.status_code}")
                                            except Exception as e:
                                                logger.error(f"[PROXY] UserData sync error for {i_id}: {repr(e)}")
                                    except Exception as e:
                                        logger.error(f"[PROXY] fix_runtime_and_sync failed for {i_id}: {repr(e)}")
                                
                                background_tasks.add_task(
                                    fix_runtime_and_sync, user_id, item_id, 
                                    position_ticks, effective_runtime_ticks, event_type,
                                    upstream_url, request, api_key
                                )
                                logger.info(f"[PROXY] 📋 Background task dispatched for {item_id} (Event: {event_type})")
                            else:
                                logger.warning(f"[PROXY] ⚠️ Missing item_id={item_id} or user_id={user_id}, cannot dispatch task")
                    except Exception as e:
                        logger.error(f"[PROXY] Failed to intercept Sessions/Playing: {repr(e)}")
            except Exception:
                logger.debug("[PROXY] handle_proxy request payload: (unable to read)")
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
            if "Infuse" in player_ua or "SenPlayer" in player_ua or "Filmly" in player_ua or ("Lavf/" in player_ua and "Lavf/60." not in player_ua):
                needs_m3u8 = True

            if needs_m3u8:
                m3u8_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,Video\n{url}\n#EXT-X-ENDLIST\n"
                return Response(content=m3u8_content.encode("utf-8"), media_type="application/vnd.apple.mpegurl")
            else:
                return RedirectResponse(url=url, status_code=302)

        # 拦截 PlaybackInfo
        if playback_info_pattern.search(full_path):
            return await _intercept_playback_info(upstream_url, api_key, full_path, request, instance)

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
        # 透传到飞牛 /v/ 入口页面，让 VidHub 能检测到飞牛服务器
        return await _proxy_request(upstream_url, api_key, "/v/", request)

    return app


# 缓存 video item 的真实时长 (ItemId -> RunTimeTicks)
_item_runtimes: dict[str, int] = {}


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
        spawn_task(_serve_proxy(server, instance.name), name=f"proxy_{instance.name}")

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