import aiofiles
import os
from app.core.media.parser import parse_filename, MediaInfo
from app.core.tmdb.client import tmdb_client
from app.config import get_config
from app.utils.helpers import sanitize_filename
from loguru import logger

from app.core.tmdb.nfo_builder import nfo_builder

class MediaOrganizer:
    def __init__(self):
        self.config = get_config().organize
        # 简单内存缓存：key为 (title, year, media_type)，value为 tmdb_data
        self._tmdb_cache = {}

    async def _search_tmdb(self, media_info: MediaInfo) -> dict:
        """带缓存的 TMDB 搜索"""
        cache_key = (media_info.title, media_info.year, media_info.media_type)
        if cache_key in self._tmdb_cache:
            return self._tmdb_cache[cache_key]

        tmdb_data = {}
        try:
            if media_info.media_type == "movie":
                results = await tmdb_client.search_movie(media_info.title, media_info.year)
                if results:
                    tmdb_data = results[0]
            else:
                results = await tmdb_client.search_tv(media_info.title, media_info.year)
                if results:
                    tmdb_data = results[0]
        except Exception as e:
            logger.error(f"TMDB search error for {media_info.title}: {e}")
            
        self._tmdb_cache[cache_key] = tmdb_data
        return tmdb_data

    async def determine_category_and_region(self, media_info: MediaInfo, tmdb_data: dict) -> tuple[str, str]:
        category = "其他"
        region = "其他"
        if media_info.media_type == "movie":
            category = "电影"
        elif media_info.media_type == "episode":
            category = "剧集"
            
        if tmdb_data:
            lang = tmdb_data.get("original_language", "")
            if lang in ["zh", "cn"]:
                region = "国产"
            elif lang in ["en", "fr", "de"]:
                region = "欧美"
            elif lang in ["ja", "ko"]:
                region = "日韩"
        return category, region

    def generate_standard_name(self, media_info: MediaInfo, tmdb_data: dict) -> tuple[str, str]:
        title = sanitize_filename(tmdb_data.get("title") or tmdb_data.get("name") or media_info.title)
        year = tmdb_data.get("release_date", "")[:4] or tmdb_data.get("first_air_date", "")[:4] or media_info.year or ""
        ext = os.path.splitext(media_info.original_filename)[1]
        
        folder_name = f"{title} ({year})" if year else title
        
        if media_info.media_type == "movie":
            file_name = f"{folder_name}{ext}"
            return folder_name, file_name
        else:
            season = media_info.season or 1
            episode = media_info.episode or 1
            file_name = f"{title} - S{season:02d}E{episode:02d}{ext}"
            return f"{folder_name}/Season {season:02d}", file_name

    async def get_organized_path(self, file_name: str) -> tuple[str, str, str, str, dict]:
        """获取标准的刮削重命名路径与TMDB数据"""
        media_info = parse_filename(file_name)
        tmdb_data = await self._search_tmdb(media_info)
        category, region = await self.determine_category_and_region(media_info, tmdb_data)
        target_folder, target_name = self.generate_standard_name(media_info, tmdb_data)
        return category, region, target_folder, target_name, tmdb_data

    async def write_nfo_file(self, target_dir: str, target_name: str, tmdb_data: dict, media_type: str):
        """生成配套的 NFO 文件"""
        if not tmdb_data:
            return

        base_name = os.path.splitext(target_name)[0]
        nfo_path = os.path.join(target_dir, f"{base_name}.nfo")

        try:
            if media_type == "movie":
                xml_content = nfo_builder.build_movie_nfo(tmdb_data)
                await nfo_builder.write_nfo(xml_content, nfo_path)

            elif media_type == "episode":
                # 父级目录的 tvshow.nfo
                tvshow_dir = os.path.dirname(target_dir) # 去掉 Season 目录
                tvshow_nfo_path = os.path.join(tvshow_dir, "tvshow.nfo")
                if not os.path.exists(tvshow_nfo_path):
                    tv_xml_content = nfo_builder.build_tvshow_nfo(tmdb_data)
                    await nfo_builder.write_nfo(tv_xml_content, tvshow_nfo_path)
                    
                # 分集暂且由 Emby 自行根据季集号提取
        except Exception as e:
            logger.error(f"Failed to build NFO for {target_name}: {e}")

    async def organize_item(self, client, item: dict, target_base_dir_id: str) -> dict:
        """
        处理单个项 (文件或目录)
        :param client: 网盘客户端实例，必须提供 create_folder, rename_file, move_files
        :param item: item 字典，包含 n (名称), fid (文件id/目录id), cid (目录id, 对于115)
        :param target_base_dir_id: 目标基础目录
        """
        # 判断是否是目录
        is_dir = "cid" in item or item.get("type") == 0 or item.get("fc", "") == "0"
        item_id = item.get("cid") if "cid" in item else item.get("fid")
        original_name = item.get("n", "")
        
        if not item_id or not original_name:
            return {"status": "error", "name": original_name, "error": "Invalid item format"}

        # 1. 刮削 & 生成规范名称
        try:
            media_info = parse_filename(original_name)
            tmdb_data = await self._search_tmdb(media_info)
            category, region, target_folder, target_name, tmdb_data = await self.get_organized_path(original_name)
            
            # 若是目录，我们要将目录重命名为 `target_folder` 的基础部分
            # 若是文件，重命名为 `target_name`
            new_name = target_folder.split('/')[0] if is_dir else target_name
            
            # 2. 网盘重命名
            if new_name and new_name != original_name:
                rename_ok = await client.rename_file(item_id, new_name)
                if not rename_ok:
                    logger.warning(f"Rename failed for {original_name} -> {new_name}")
            else:
                new_name = original_name

            # 3. 在目标盘中逐层创建分类目录: target_base_dir_id / category / region
            # (如果需要，还可以继续创建目标文件夹)
            # 因为是目录整体移动，我们通常把子目录直接移入 region 目录下即可
            
            current_parent_id = target_base_dir_id
            for sub in [category, region]:
                res = await client.create_folder(current_parent_id, sub)
                if res and res.get("cid"):
                    current_parent_id = res.get("cid")
                else:
                    logger.error(f"Failed to create folder {sub} under {current_parent_id}")
                    break
            
            target_parent_id = current_parent_id
            
            # 若是文件，还要根据 generate_standard_name 返回的 target_folder 创建专属目录
            if not is_dir and target_folder:
                # 处理多级文件夹，如 "Title (Year)/Season 01"
                for sub in target_folder.split('/'):
                    if not sub:
                        continue
                    res = await client.create_folder(target_parent_id, sub)
                    if res and res.get("cid"):
                        target_parent_id = res.get("cid")

            # 4. 移动
            move_ok = await client.move_files([item_id], target_parent_id)
            if not move_ok:
                return {"status": "error", "name": new_name, "error": "Move failed"}

            # 5. 如果是文件，生成 NFO (如果是目录就不在移动过程中单独生成了)
            # 暂时无法直接写入网盘 NFO，只能提示完成
            # TODO: 将 nfo 上传至网盘
            
            return {
                "status": "success",
                "original_name": original_name,
                "new_name": new_name,
                "target_folder": target_folder
            }

        except Exception as e:
            logger.error(f"Organize item {original_name} failed: {e}")
            return {"status": "error", "name": original_name, "error": str(e)}

organizer = MediaOrganizer()
