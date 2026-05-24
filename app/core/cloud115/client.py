from typing import List, Dict, Optional, Any, Tuple
import time
from loguru import logger
from app.core.cloud115.auth import auth_manager
import asyncio

class Cloud115Client:
    def __init__(self):
        self.auth = auth_manager
        # 限制并发
        self.semaphore = asyncio.Semaphore(5)
        # 内存缓存：key为 'pickcode|user_agent'，value为 (到期时间戳, 直链url)
        self._url_cache: Dict[str, Tuple[float, str]] = {}

    @property
    def client(self):
        return self.auth.client

    async def list_files(self, dir_id: str = '0', limit: int = 100, offset: int = 0) -> dict:
        """列出目录文件"""
        if not self.client:
            return {"error": "Client not initialized"}
        async with self.semaphore:
            try:
                res = await self.client.fs_files({"cid": dir_id, "limit": limit, "offset": offset}, async_=True)
                if res.get("state"):
                    return {
                        "total": res.get("count", 0),
                        "items": res.get("data", [])
                    }
                return {"error": res.get("error", "Unknown error")}
            except Exception as e:
                logger.error(f"Failed to list files for dir {dir_id}: {e}")
                return {"error": str(e)}

    async def list_dirs(self, dir_id: str = '0') -> dict:
        """列出目录，仅包含文件夹，排除文件"""
        if not self.client:
            return {"error": "Client not initialized"}
        async with self.semaphore:
            try:
                # 尽量拉取更多数据以确保文件夹不会因分页被遗漏
                res = await self.client.fs_files({"cid": dir_id, "limit": 1000, "offset": 0}, async_=True)
                if res.get("state"):
                    items = res.get("data", [])
                    # 在 115 响应中，文件通常包含 "fid"，文件夹包含 "cid" 且无 "fid"
                    dirs = [
                        {"cid": str(item.get("cid")), "n": item.get("n", ""), "pid": str(item.get("pid", "0"))} 
                        for item in items if "fid" not in item
                    ]
                    
                    # 尝试从路径推断当前目录名称和父目录ID
                    path_list = res.get("path", [])
                    current_dir_name = "根目录"
                    parent_id = "0"
                    
                    if path_list and len(path_list) > 0:
                        current_dir_name = path_list[-1].get("name", "根目录")
                        if len(path_list) > 1:
                            parent_id = str(path_list[-2].get("cid", "0"))
                    
                    return {
                        "current_dir": current_dir_name,
                        "parent_id": parent_id,
                        "dirs": dirs
                    }
                return {"error": res.get("error", "Unknown error")}
            except Exception as e:
                logger.error(f"Failed to list dirs for dir {dir_id}: {e}")
                return {"error": str(e)}

    async def create_folder(self, parent_id: str, name: str) -> dict:
        """创建文件夹"""
        if not self.client:
            return {"error": "Client not initialized"}
        async with self.semaphore:
            try:
                res = await self.client.fs_mkdir(name, parent_id, async_=True)
                if res.get("state"):
                    # 115可能返回file_id
                    return {"id": res.get("file_id"), "name": name}
                return {"error": res.get("error", "Unknown error")}
            except Exception as e:
                logger.error(f"Failed to create folder {name}: {e}")
                return {"error": str(e)}

    async def rename_file(self, file_id: str, new_name: str) -> bool:
        """重命名文件/文件夹"""
        if not self.client:
            return False
        async with self.semaphore:
            try:
                res = await self.client.fs_rename((file_id, new_name), async_=True)
                return res.get("state", False)
            except Exception as e:
                logger.error(f"Failed to rename file {file_id}: {e}")
                return False

    async def move_files(self, file_ids: List[str], target_dir_id: str) -> bool:
        """移动文件/文件夹"""
        if not self.client or not file_ids:
            return False
        async with self.semaphore:
            try:
                res = await self.client.fs_move(file_ids, target_dir_id, async_=True)
                return res.get("state", False)
            except Exception as e:
                logger.error(f"Failed to move files {file_ids}: {e}")
                return False

    async def delete_files(self, file_ids: List[str]) -> bool:
        """删除文件/文件夹到回收站"""
        if not self.client or not file_ids:
            return False
        async with self.semaphore:
            try:
                res = await self.client.fs_delete(file_ids, async_=True)
                return res.get("state", False)
            except Exception as e:
                logger.error(f"Failed to delete files {file_ids}: {e}")
                return False

    async def get_download_url(self, pickcode: str, user_agent: Optional[str] = None) -> str:
        """获取直链 (可能包含RSA解密过程) 并提供2小时的内存缓存防封控"""
        if not self.client:
            logger.error("get_download_url failed: client is not initialized")
            return ""
            
        # 检查缓存
        cache_key = f"{pickcode}|{user_agent or ''}"
        now = time.time()
        if cache_key in self._url_cache:
            expire_time, cached_url = self._url_cache[cache_key]
            if now < expire_time:
                logger.info(f"🎯 [CACHE HIT] Memory cache hit for pickcode: {pickcode} (UA: {user_agent})")
                return cached_url
            else:
                logger.debug(f"🗑️ [CACHE EXPIRED] Memory cache expired for pickcode: {pickcode} (UA: {user_agent})")
                # 缓存已过期，清理掉
                del self._url_cache[cache_key]
                
        async with self.semaphore:
            try:
                logger.info(f"🚀 [API CALL] Requesting new download URL from 115 for pickcode: {pickcode} (UA: {user_agent})")
                # 透传客户端真实的 UA，打破 115 的直链 UA 防盗链绑定机制
                kwargs = {}
                if user_agent:
                    kwargs['user_agent'] = user_agent
                result = await asyncio.to_thread(self.client.download_url, pickcode, **kwargs)
                url_str = str(result)
                
                # 请求成功，存入缓存，有效期设定为 2 小时 (7200秒)
                if url_str:
                    logger.success(f"✅ [SUCCESS] Generated new download URL for pickcode: {pickcode}")
                    self._url_cache[cache_key] = (now + 7200, url_str)
                else:
                    logger.warning(f"⚠️ [WARNING] 115 API returned empty URL for pickcode: {pickcode}")
                    
                return url_str
            except Exception as e:
                logger.error(f"❌ [ERROR] Failed to get download url for pickcode {pickcode}: {e}")
                return ""

    async def offline_add_url(self, url: str, target_dir_id: str = "0") -> Dict[str, Any]:
        """将磁力链、种子链接或HTTP链接添加到115离线下载"""
        if not self.client:
            return {"state": False, "error": "Client not initialized"}
        async with self.semaphore:
            try:
                # 115 离线下载要求传入 target_dir_id 为 string，如果是 "0" 代表根目录
                logger.info(f"Adding offline task to 115: {url} -> dir: {target_dir_id}")
                result = await asyncio.to_thread(
                    self.client.offline_add_url,
                    url,
                    payload={"wp_path_id": target_dir_id}
                )
                if isinstance(result, dict) and result.get("state"):
                    return {"state": True, "info_hash": result.get("info_hash"), "name": result.get("name")}
                return {"state": False, "error": result.get("error_msg", "Unknown error"), "raw": result}
            except Exception as e:
                logger.error(f"Failed to add offline task: {e}")
                return {"state": False, "error": str(e)}

    async def share_receive(self, share_url: str, receive_code: str, target_dir_id: str = "0") -> Dict[str, Any]:
        """转存115分享链接"""
        if not self.client:
            return {"state": False, "error": "Client not initialized"}
        async with self.semaphore:
            try:
                logger.info(f"Receiving share link: {share_url}")
                # p115client 的 share_receive 可能需要先解析出 share_code 和 receive_code，这里需要自行提取
                # 但是 p115client 也提供了高级封装 share_receive_app 或直接传入 share_url，取决于版本
                # 我们暂时使用 p115client.share_skip_login_down 或其他适合的 API，如果 p115client 支持一键转存的话
                # 为了兼容性，使用底层的转存逻辑
                from urllib.parse import urlparse, parse_qs
                
                # 提取 share_code
                share_code = ""
                if "s/" in share_url:
                    share_code = share_url.split("s/")[1].split("?")[0]
                elif "share_code=" in share_url:
                    parsed_url = urlparse(share_url)
                    qs = parse_qs(parsed_url.query)
                    if "share_code" in qs:
                        share_code = qs["share_code"][0]
                
                if not share_code:
                    return {"state": False, "error": "Invalid share URL format"}
                    
                # 提取文件 ID：这通常需要先获取分享信息
                share_info = await asyncio.to_thread(self.client.share_info, share_code, receive_code)
                if not share_info.get("state"):
                    return {"state": False, "error": share_info.get("error_msg", "Failed to get share info")}
                    
                # 获取根目录的所有 file_id
                file_ids = []
                for item in share_info.get("data", {}).get("list", []):
                    file_ids.append(item.get("f", "") or item.get("fid", ""))
                    
                file_ids = [fid for fid in file_ids if fid]
                
                if not file_ids:
                    return {"state": False, "error": "No files found in share"}
                    
                # 构造 payload 进行转存
                payload = {
                    "share_code": share_code,
                    "receive_code": receive_code,
                    "file_id": ",".join(file_ids),
                    "cid": target_dir_id
                }
                result = await asyncio.to_thread(self.client.share_receive, payload)
                if result.get("state"):
                    return {"state": True, "msg": "转存成功"}
                return {"state": False, "error": result.get("error_msg", "Transfer failed"), "raw": result}
            except Exception as e:
                logger.error(f"Failed to receive share: {e}")
                return {"state": False, "error": str(e)}

    async def get_offline_tasks(self) -> List[Dict]:
        """获取正在进行的离线下载任务"""
        if not self.client:
            return []
        async with self.semaphore:
            try:
                # 获取离线任务列表
                result = await asyncio.to_thread(self.client.offline_list)
                if isinstance(result, dict) and result.get("state"):
                    tasks = result.get("tasks", [])
                    return tasks
                return []
            except Exception as e:
                logger.error(f"Failed to get offline tasks: {e}")
                return []

# 单例
client_115 = Cloud115Client()
