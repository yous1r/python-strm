from fastapi import Request, Response
from fastapi.responses import RedirectResponse
import httpx
import re
from loguru import logger
from app.core.emby.manager import emby_manager
from app.core.cloud115.client import client_115
import urllib.parse

class EmbyProxy:
    def __init__(self):
        # 匹配视频流请求
        self.video_stream_pattern = re.compile(r'/videos/(\w+)/stream', re.IGNORECASE)
        # 匹配 PlaybackInfo 请求
        self.playback_info_pattern = re.compile(r'/Items/(\w+)/PlaybackInfo', re.IGNORECASE)

    async def handle_request(self, instance_id: str, path: str, request: Request) -> Response:
        """处理Emby请求"""
        instance = await emby_manager.get_instance(instance_id)
        if not instance:
            return Response(status_code=404, content="Emby instance not found")
            
        full_path = f"/emby/{path}" if not path.startswith("emby/") else f"/{path}"
        
        # 检查是否为视频播放请求
        match = self.video_stream_pattern.search(full_path)
        if match:
            item_id = match.group(1)
            logger.info(f"Intercepted playback request for item {item_id}")
            
            # 尝试解析真实播放地址 (302)
            redirect_url = await self._resolve_playback_url(instance, item_id)
            if redirect_url:
                logger.info(f"Redirecting play request to {redirect_url}")
                return RedirectResponse(url=redirect_url, status_code=302)
                
        # 检查是否为 PlaybackInfo 请求 (核心黑科技：注入真实 115 直链)
        if self.playback_info_pattern.search(full_path):
            return await self._intercept_playback_info(instance, full_path, request)
        
        # 普通请求代理转发
        return await self._proxy_request(instance, full_path, request)

    async def _intercept_playback_info(self, instance: dict, path: str, request: Request) -> Response:
        """拦截 PlaybackInfo 请求，硬塞 115 直链以绕过探针"""
        resp = await self._proxy_request(instance, path, request)
        if resp.status_code != 200:
            return resp
            
        try:
            import json
            data = json.loads(resp.body)
            modified = False
            
            client_ua = request.headers.get("user-agent", "Unknown")
            media_sources = data.get("MediaSources", [])
            
            for source in media_sources:
                for key in ["Path", "DirectStreamUrl"]:
                    url = source.get(key, "")
                    if url and "/api/v1/115/play/" in url:
                        # 提取 pickcode
                        match = re.search(r'/api/v1/115/play/([^/|?]+)', url)
                        if match:
                            pickcode = match.group(1)
                            # 获取真实 115 CDN 直链
                            real_url = await client_115.get_download_url(pickcode, user_agent=client_ua)
                            if real_url:
                                source[key] = real_url
                                source["IsRemote"] = True
                                source["Protocol"] = "Http"
                                modified = True
                                logger.info(f"🎯 [PROXY] Injected 115 CDN URL for pickcode {pickcode} into PlaybackInfo (UA: {client_ua})")
            
            if modified:
                content = json.dumps(data).encode("utf-8")
                headers = dict(resp.headers)
                headers["content-length"] = str(len(content))
                headers.pop("content-encoding", None) # 确保没有残留的 gzip 头
                return Response(content=content, status_code=200, headers=headers)
                
        except Exception as e:
            logger.error(f"Failed to modify PlaybackInfo JSON: {e}")
            
        return resp

    async def _resolve_playback_url(self, instance: dict, item_id: str) -> str:
        """解析出真实播放地址"""
        try:
            # 1. 获取Item的Path
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    f"{instance['url']}/emby/Items/{item_id}",
                    params={"api_key": instance['api_key']}
                )
                if res.status_code != 200:
                    return None
                    
                item_data = res.json()
                path = item_data.get("Path", "")
                
                # 如果是 strm 文件，读取其中的 pickcode
                if path.endswith(".strm"):
                    # TODO: 从数据库或本地存储读取strm内容
                    pass
                    
            return None
        except Exception as e:
            logger.error(f"Failed to resolve playback url: {e}")
            return None

    async def _proxy_request(self, instance: dict, path: str, request: Request) -> Response:
        """透明代理请求到真实的Emby服务器"""
        try:
            url = f"{instance['url']}{path}"
            params = dict(request.query_params)
            # 确保带有api_key
            if "api_key" not in params:
                params["api_key"] = instance["api_key"]
                
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
                
                # HttpX 会处理返回体，为了避免压缩问题我们过滤掉一些头部
                resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'content-length', 'transfer-encoding']}
                
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers
                )
        except Exception as e:
            logger.error(f"Proxy request failed: {e}")
            return Response(status_code=502, content="Bad Gateway")

emby_proxy = EmbyProxy()
