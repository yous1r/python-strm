import asyncio
from typing import List, Dict, Optional
import httpx
from loguru import logger
from app.core.cloud123.auth import auth_manager_123

class Cloud123Client:
    def __init__(self):
        self.auth = auth_manager_123
        self.semaphore = asyncio.Semaphore(5)
        self.base_url = "https://www.123pan.com/api"
        # 也可以使用 openapi: https://openapi.123pan.com/api/v1 
        # 此处我们基于web接口或者OpenAPI实现核心功能。为稳定起见，假设使用的是标准的Web API结构

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.auth.access_token}",
            "Platform": "mac"
        }

    async def list_files(self, parent_id: str = "0", limit: int = 100, page: int = 1) -> dict:
        """列出目录文件"""
        if not self.auth.access_token:
            return {"error": "Token not initialized"}
            
        async with self.semaphore:
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.get(
                        f"{self.base_url}/file/list", 
                        headers=self.headers,
                        params={
                            "parent_id": parent_id,
                            "limit": limit,
                            "page": page,
                            "order_by": "file_name",
                            "order_direction": "asc"
                        }
                    )
                    data = res.json()
                    if data.get("code") == 0:
                        file_list = data.get("data", {}).get("file_list", [])
                        return {
                            "total": data.get("data", {}).get("total", len(file_list)),
                            "items": file_list
                        }
                    return {"error": data.get("message", "Unknown error")}
            except Exception as e:
                logger.error(f"Failed to list files for dir {parent_id}: {e}")
                return {"error": str(e)}

    async def get_download_url(self, file_id: str) -> str:
        """获取直链"""
        if not self.auth.access_token:
            return ""
        
        async with self.semaphore:
            try:
                async with httpx.AsyncClient() as client:
                    # 123pan 获取直链一般调用 /file/download_info 或类似接口
                    res = await client.post(
                        f"{self.base_url}/file/download_info",
                        headers=self.headers,
                        json={"file_id": file_id}
                    )
                    data = res.json()
                    if data.get("code") == 0:
                        return data.get("data", {}).get("download_url", "")
                    logger.warning(f"123pan get_download_url err: {data.get('message')}")
                    return ""
            except Exception as e:
                logger.error(f"Failed to get download url for {file_id}: {e}")
                return ""

    async def rename_file(self, file_id: str, new_name: str) -> bool:
        """重命名文件/文件夹 (123pan暂不实现)"""
        logger.warning("123pan 暂未实现 rename_file")
        return True

    async def create_folder(self, parent_id: str, name: str) -> dict:
        """创建文件夹 (123pan暂不实现)"""
        logger.warning("123pan 暂未实现 create_folder")
        return {"cid": parent_id}

    async def move_files(self, file_ids: List[str], target_dir_id: str) -> bool:
        """移动文件/文件夹 (123pan暂不实现)"""
        logger.warning("123pan 暂未实现 move_files")
        return True

# 单例
client_123 = Cloud123Client()
