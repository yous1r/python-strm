from typing import List, Dict, Optional, Any
from loguru import logger
from app.core.cloud115.auth import auth_manager
import asyncio

class Cloud115Client:
    def __init__(self):
        self.auth = auth_manager
        # 限制并发
        self.semaphore = asyncio.Semaphore(5)

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
        """获取直链 (可能包含RSA解密过程)"""
        if not self.client:
            return ""
        async with self.semaphore:
            try:
                # 透传客户端真实的 UA，打破 115 的直链 UA 防盗链绑定机制
                kwargs = {}
                if user_agent:
                    kwargs['user_agent'] = user_agent
                result = await asyncio.to_thread(self.client.download_url, pickcode, **kwargs)
                # p115client 返回的是 P115URL 对象，强转为 string
                return str(result)
            except Exception as e:
                logger.error(f"Failed to get download url for {pickcode}: {e}")
                return ""

# 单例
client_115 = Cloud115Client()
