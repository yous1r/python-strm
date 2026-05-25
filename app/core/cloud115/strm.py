import os
import asyncio
import aiofiles
from loguru import logger
from app.core.cloud115.client import client_115
from app.utils.helpers import is_video_file
from app.core.media.organizer import organizer
from app.config import get_config

class StrmGenerator115:
    def __init__(self):
        self.client = client_115

    async def generate_strm(self, pickcode: str, file_name: str, current_dir: str, root_dir: str, base_url: str) -> str:
        """生成单个STRM文件，支持智能刮削打平"""
        config = get_config()
        
        import urllib.parse
        encoded_name = urllib.parse.quote(file_name)
        strm_content = f"{base_url.rstrip('/')}/api/v1/115/play/{pickcode}/{encoded_name}"
        if config.cloud115.play_ua:
            strm_content += f"|User-Agent={config.cloud115.play_ua}"
        
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
            # 原生模式：保留网盘的目录嵌套结构
            base_name = os.path.splitext(file_name)[0]
            strm_filename = f"{base_name}.strm"
            strm_path = os.path.join(current_dir, strm_filename)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(strm_path), exist_ok=True)
        
        try:
            async with aiofiles.open(strm_path, mode='w', encoding='utf-8') as f:
                await f.write(strm_content)
            return strm_path
        except Exception as e:
            logger.error(f"Failed to write STRM file {strm_path}: {e}")
            return ""

    async def batch_generate(self, dir_id: str, output_dir: str, base_url: str, recursive: bool = True, root_output_dir: str = None, force: bool = False) -> list[str]:
        """批量生成STRM文件"""
        if root_output_dir is None:
            root_output_dir = output_dir

        generated = []
        limit = 1000
        offset = 0

        while True:
            # 防风控：增量跳过太快会导致 list_files 并发超限触发阿里云 WAF 405
            await asyncio.sleep(0.2)
            
            res = await self.client.list_files(dir_id=dir_id, limit=limit, offset=offset)
            if "error" in res:
                logger.error(f"Batch generate error: {res['error']}")
                break

            items = res.get("items", [])
            if not items:
                break
                
            # If force is False, query the database for existing items in this batch
            existing_fids = set()
            if not force:
                file_ids = [str(item.get("fid")) for item in items if "fid" in item]
                if file_ids:
                    from app.database import get_db_conn
                    try:
                        async with get_db_conn() as db:
                            placeholders = ",".join(["?"] * len(file_ids))
                            query = f"SELECT file_id FROM strm_records WHERE cloud_type='115' AND file_id IN ({placeholders})"
                            async with db.execute(query, file_ids) as cursor:
                                rows = await cursor.fetchall()
                                existing_fids = {str(row["file_id"]) for row in rows}
                    except Exception as e:
                        logger.error(f"Failed to fetch existing fids: {e}")
                
            for item in items:
                # 文件夹处理
                if "fid" not in item:
                    if recursive:
                        folder_name = item.get("n", "")
                        folder_id = str(item.get("cid"))
                        sub_dir = os.path.join(output_dir, folder_name)
                        # 递归遍历子目录，但传入统一的 root_output_dir 以便于打平结构
                        sub_generated = await self.batch_generate(folder_id, sub_dir, base_url, recursive, root_output_dir, force)
                        generated.extend(sub_generated)
                else:
                    # 文件处理
                    file_id = str(item.get("fid", ""))
                    if file_id in existing_fids and not force:
                        logger.debug(f"Skipping already generated file: {item.get('n')}")
                        continue
                        
                    file_name = item.get("n", "")
                    if is_video_file(file_name):
                        pickcode = item.get("pc", "")
                        if pickcode:
                            strm_path = await self.generate_strm(pickcode, file_name, output_dir, root_output_dir, base_url)
                            if strm_path:
                                generated.append(strm_path)
                                # Record to DB
                                try:
                                    from app.database import get_db_conn
                                    async with get_db_conn() as db:
                                        await db.execute('''
                                            INSERT OR IGNORE INTO strm_records (file_id, cloud_type, strm_path)
                                            VALUES (?, ?, ?)
                                        ''', (file_id, '115', strm_path))
                                        await db.commit()
                                except Exception as e:
                                    logger.error(f"Failed to record STRM in DB: {e}")
                                
            # 分页逻辑
            if len(items) < limit:
                break
            offset += limit
            
        return generated

generator_115 = StrmGenerator115()
