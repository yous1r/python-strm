import os
import aiofiles
from loguru import logger
from app.core.cloud123.client import client_123
from app.utils.helpers import is_video_file
from app.core.media.organizer import organizer
from app.config import get_config

class StrmGenerator123:
    def __init__(self):
        self.client = client_123

    async def generate_strm(self, file_id: str, file_name: str, current_dir: str, root_dir: str, base_url: str) -> str:
        """生成单个STRM文件"""
        strm_content = f"{base_url.rstrip('/')}/api/v1/123/play/{file_id}"
        
        config = get_config()
        if config.organize.enabled:
            # 智能整理模式：忽略网盘原生路径，打平为 大类/地区/特定名称
            category, region, target_folder, target_name, tmdb_data = await organizer.get_organized_path(file_name)
            target_dir = os.path.join(root_dir, category, region, target_folder)
            
            base_name = os.path.splitext(target_name)[0]
            strm_filename = f"{base_name}.strm"
            strm_path = os.path.join(target_dir, strm_filename)
            
            # 同时生成 NFO
            media_type = "movie" if category == "电影" else "episode"
            await organizer.write_nfo_file(target_dir, target_name, tmdb_data, media_type)
        else:
            base_name = os.path.splitext(file_name)[0]
            strm_filename = f"{base_name}.strm"
            strm_path = os.path.join(current_dir, strm_filename)
        
        os.makedirs(os.path.dirname(strm_path), exist_ok=True)
        
        try:
            async with aiofiles.open(strm_path, mode='w', encoding='utf-8') as f:
                await f.write(strm_content)
            return strm_path
        except Exception as e:
            logger.error(f"Failed to write STRM file {strm_path}: {e}")
            return ""

    async def batch_generate(self, dir_id: str, output_dir: str, base_url: str, recursive: bool = True, root_output_dir: str = None) -> list[str]:
        """批量生成STRM文件"""
        if root_output_dir is None:
            root_output_dir = output_dir
            
        generated = []
        limit = 100
        page = 1
        
        while True:
            res = await self.client.list_files(parent_id=dir_id, limit=limit, page=page)
            if "error" in res:
                logger.error(f"Batch generate error: {res['error']}")
                break
                
            items = res.get("items", [])
            if not items:
                break
                
            for item in items:
                # type=1 为文件夹，type=0 为文件 (123pan通用约定)
                is_dir = item.get("type") == 1
                
                if is_dir:
                    if recursive:
                        folder_name = item.get("file_name", "")
                        folder_id = str(item.get("file_id"))
                        sub_dir = os.path.join(output_dir, folder_name)
                        sub_generated = await self.batch_generate(folder_id, sub_dir, base_url, recursive, root_output_dir)
                        generated.extend(sub_generated)
                else:
                    file_name = item.get("file_name", "")
                    if is_video_file(file_name):
                        file_id = str(item.get("file_id"))
                        strm_path = await self.generate_strm(file_id, file_name, output_dir, root_output_dir, base_url)
                        if strm_path:
                            generated.append(strm_path)
                            
            if len(items) < limit:
                break
            page += 1
            
        return generated

generator_123 = StrmGenerator123()
