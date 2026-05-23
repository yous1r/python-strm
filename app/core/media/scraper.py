import os
import aiofiles
from loguru import logger
import xml.etree.ElementTree as ET
from xml.dom import minidom
from app.core.media.parser import MediaInfo

class MetadataScraper:
    async def generate_movie_nfo(self, media_info: MediaInfo, tmdb_data: dict, output_path: str) -> bool:
        """生成电影NFO"""
        if not tmdb_data:
            return False
            
        try:
            movie = ET.Element("movie")
            ET.SubElement(movie, "title").text = tmdb_data.get("title", media_info.title)
            if "original_title" in tmdb_data:
                ET.SubElement(movie, "originaltitle").text = tmdb_data["original_title"]
                
            if tmdb_data.get("id"):
                uid = ET.SubElement(movie, "uniqueid", type="tmdb", default="true")
                uid.text = str(tmdb_data["id"])
                
            if "release_date" in tmdb_data and tmdb_data["release_date"]:
                ET.SubElement(movie, "year").text = tmdb_data["release_date"][:4]
                ET.SubElement(movie, "premiered").text = tmdb_data["release_date"]
                
            if "overview" in tmdb_data:
                ET.SubElement(movie, "plot").text = tmdb_data["overview"]
                
            # 美化XML
            rough_string = ET.tostring(movie, encoding="unicode")
            reparsed = minidom.parseString(rough_string)
            xml_str = reparsed.toprettyxml(indent="    ")
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            async with aiofiles.open(output_path, mode='w', encoding='utf-8') as f:
                await f.write(xml_str)
            return True
        except Exception as e:
            logger.error(f"Generate movie NFO failed: {e}")
            return False

    async def generate_tv_nfo(self, tmdb_data: dict, output_path: str) -> bool:
        """生成剧集NFO"""
        if not tmdb_data:
            return False
            
        try:
            tvshow = ET.Element("tvshow")
            ET.SubElement(tvshow, "title").text = tmdb_data.get("name", "")
            if "original_name" in tmdb_data:
                ET.SubElement(tvshow, "originaltitle").text = tmdb_data["original_name"]
                
            if tmdb_data.get("id"):
                uid = ET.SubElement(tvshow, "uniqueid", type="tmdb", default="true")
                uid.text = str(tmdb_data["id"])
                
            if "overview" in tmdb_data:
                ET.SubElement(tvshow, "plot").text = tmdb_data["overview"]
                
            rough_string = ET.tostring(tvshow, encoding="unicode")
            reparsed = minidom.parseString(rough_string)
            xml_str = reparsed.toprettyxml(indent="    ")
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            async with aiofiles.open(output_path, mode='w', encoding='utf-8') as f:
                await f.write(xml_str)
            return True
        except Exception as e:
            logger.error(f"Generate TV NFO failed: {e}")
            return False

scraper = MetadataScraper()
