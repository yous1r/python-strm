from tmdbv3api import TMDb, Movie, TV
import asyncio
from loguru import logger
from app.config import get_config

class TmdbClient:
    def __init__(self):
        self.config = get_config().tmdb
        self.tmdb = TMDb()
        self.tmdb.api_key = self.config.api_key
        self.tmdb.language = self.config.language
        self.tmdb.debug = False
        
        if self.config.proxy:
            # 解决 tmdbv3api 内部使用 lru_cache 导致代理字典 unhashable 的 Bug
            original_cached_request = TMDb.cached_request
            
            def patched_cached_request(method, url, data, json, proxies):
                my_proxies = {
                    "http": self.config.proxy,
                    "https": self.config.proxy
                }
                return original_cached_request.__wrapped__(method, url, data, json, my_proxies)
                
            TMDb.cached_request = staticmethod(patched_cached_request)
        
        self.movie_api = Movie()
        self.tv_api = TV()
        
    async def search_movie(self, title: str, year: int = None) -> list:
        """搜索电影"""
        if not self.tmdb.api_key:
            return []
        try:
            results = await asyncio.to_thread(self.movie_api.search, title)
            if not results:
                return []
            
            if year:
                filtered = []
                for m in results:
                    if hasattr(m, 'release_date') and m.release_date and m.release_date.startswith(str(year)):
                        filtered.append(m)
                if filtered:
                    results = filtered
                    
            final_res = []
            for m in results:
                if hasattr(m, '__dict__'):
                    final_res.append(m.__dict__)
                elif isinstance(m, dict):
                    final_res.append(m)
            return final_res
        except Exception as e:
            logger.error(f"TMDB movie search failed: {e}")
            return []

    async def search_tv(self, title: str, year: int = None) -> list:
        """搜索剧集"""
        if not self.tmdb.api_key:
            return []
        try:
            results = await asyncio.to_thread(self.tv_api.search, title)
            if not results:
                return []
            
            if year:
                filtered = []
                for t in results:
                    if hasattr(t, 'first_air_date') and t.first_air_date and t.first_air_date.startswith(str(year)):
                        filtered.append(t)
                if filtered:
                    results = filtered
                    
            final_res = []
            for t in results:
                if hasattr(t, '__dict__'):
                    final_res.append(t.__dict__)
                elif isinstance(t, dict):
                    final_res.append(t)
            return final_res
        except Exception as e:
            logger.error(f"TMDB TV search failed: {e}")
            return []

    async def get_movie_detail(self, tmdb_id: int) -> dict:
        """获取电影详情"""
        if not self.tmdb.api_key:
            return {}
        try:
            detail = await asyncio.to_thread(self.movie_api.details, tmdb_id)
            return detail.__dict__
        except Exception as e:
            logger.error(f"TMDB movie detail failed: {e}")
            return {}

    async def get_tv_detail(self, tmdb_id: int) -> dict:
        """获取剧集详情"""
        if not self.tmdb.api_key:
            return {}
        try:
            detail = await asyncio.to_thread(self.tv_api.details, tmdb_id)
            return detail.__dict__
        except Exception as e:
            logger.error(f"TMDB tv detail failed: {e}")
            return {}

# 单例
tmdb_client = TmdbClient()
