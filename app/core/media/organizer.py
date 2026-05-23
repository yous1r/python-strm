import aiofiles
import os
from app.core.media.parser import parse_filename, MediaInfo
from app.core.tmdb.client import tmdb_client
from app.config import get_config
from app.utils.helpers import sanitize_filename
from loguru import logger

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
        
        title = tmdb_data.get("title") or tmdb_data.get("name", "")
        original_title = tmdb_data.get("original_title") or tmdb_data.get("original_name", "")
        plot = tmdb_data.get("overview", "")
        year = tmdb_data.get("release_date", "")[:4] or tmdb_data.get("first_air_date", "")[:4]
        tmdbid = tmdb_data.get("id", "")

        try:
            os.makedirs(target_dir, exist_ok=True)
            if media_type == "movie":
                xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
  <title>{title}</title>
  <originaltitle>{original_title}</originaltitle>
  <year>{year}</year>
  <plot>{plot}</plot>
  <tmdbid>{tmdbid}</tmdbid>
</movie>'''
                async with aiofiles.open(nfo_path, 'w', encoding='utf-8') as f:
                    await f.write(xml_content)
                    
            elif media_type == "episode":
                # 分集 NFO (仅占位，Emby依靠tvshow.nfo和文件名就可刮出具体集信息)
                xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<episodedetails>
  <title>Episode</title>
</episodedetails>'''
                async with aiofiles.open(nfo_path, 'w', encoding='utf-8') as f:
                    await f.write(xml_content)
                
                # 父级目录的 tvshow.nfo
                tvshow_dir = os.path.dirname(target_dir) # 也就是 target_folder (去掉Season)
                tvshow_nfo_path = os.path.join(tvshow_dir, "tvshow.nfo")
                if not os.path.exists(tvshow_nfo_path):
                    tv_xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<tvshow>
  <title>{title}</title>
  <originaltitle>{original_title}</originaltitle>
  <year>{year}</year>
  <plot>{plot}</plot>
  <tmdbid>{tmdbid}</tmdbid>
</tvshow>'''
                    async with aiofiles.open(tvshow_nfo_path, 'w', encoding='utf-8') as f:
                        await f.write(tv_xml_content)
        except Exception as e:
            logger.error(f"Failed to write NFO for {title}: {e}")

organizer = MediaOrganizer()
