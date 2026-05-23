import os
import aiofiles
import xml.etree.ElementTree as ET
from xml.dom import minidom
from loguru import logger

class NfoBuilder:
    @staticmethod
    def _create_text_element(parent, tag, text):
        if text is not None and str(text).strip():
            el = ET.SubElement(parent, tag)
            el.text = str(text)
            return el
        return None

    @staticmethod
    def build_movie_nfo(movie_data: dict) -> str:
        """根据 TMDB 电影数据生成 movie.nfo XML 字符串"""
        root = ET.Element('movie')
        
        NfoBuilder._create_text_element(root, 'title', movie_data.get('title'))
        NfoBuilder._create_text_element(root, 'originaltitle', movie_data.get('original_title'))
        NfoBuilder._create_text_element(root, 'plot', movie_data.get('overview'))
        
        if movie_data.get('release_date'):
            year = movie_data['release_date'].split('-')[0]
            NfoBuilder._create_text_element(root, 'year', year)
            NfoBuilder._create_text_element(root, 'premiered', movie_data['release_date'])
            
        if movie_data.get('poster_path'):
            poster_url = f"https://image.tmdb.org/t/p/original{movie_data['poster_path']}"
            NfoBuilder._create_text_element(root, 'thumb', poster_url)
            
        NfoBuilder._create_text_element(root, 'tmdbid', movie_data.get('id'))
        
        # 格式化 XML
        xmlstr = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(indent="  ")
        return xmlstr

    @staticmethod
    def build_tvshow_nfo(tv_data: dict) -> str:
        """根据 TMDB 剧集数据生成 tvshow.nfo XML 字符串"""
        root = ET.Element('tvshow')
        
        NfoBuilder._create_text_element(root, 'title', tv_data.get('name'))
        NfoBuilder._create_text_element(root, 'originaltitle', tv_data.get('original_name'))
        NfoBuilder._create_text_element(root, 'plot', tv_data.get('overview'))
        
        if tv_data.get('first_air_date'):
            year = tv_data['first_air_date'].split('-')[0]
            NfoBuilder._create_text_element(root, 'year', year)
            NfoBuilder._create_text_element(root, 'premiered', tv_data['first_air_date'])
            
        if tv_data.get('poster_path'):
            poster_url = f"https://image.tmdb.org/t/p/original{tv_data['poster_path']}"
            NfoBuilder._create_text_element(root, 'thumb', poster_url)
            
        NfoBuilder._create_text_element(root, 'tmdbid', tv_data.get('id'))
        
        # 格式化 XML
        xmlstr = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(indent="  ")
        return xmlstr

    @staticmethod
    async def write_nfo(xml_content: str, output_path: str):
        """将生成的 XML 写入文件"""
        if not xml_content:
            return
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            async with aiofiles.open(output_path, 'w', encoding='utf-8') as f:
                await f.write(xml_content)
            logger.debug(f"Saved NFO to {output_path}")
        except Exception as e:
            logger.error(f"Failed to write NFO {output_path}: {e}")

nfo_builder = NfoBuilder()
