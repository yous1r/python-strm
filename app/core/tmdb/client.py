import asyncio

import requests
import tmdbsimple as tmdb
from loguru import logger

from app.config import get_config


class TmdbClient:
    def __init__(self):
        self.config = get_config().tmdb
        self._configure_tmdb()
        self.search_api = tmdb.Search()

    def _configure_tmdb(self):
        tmdb.API_KEY = self.config.api_key
        if self.config.proxy:
            session = requests.Session()
            session.proxies.update(
                {
                    "http": self.config.proxy,
                    "https": self.config.proxy,
                }
            )
            tmdb.REQUESTS_SESSION = session
        else:
            tmdb.REQUESTS_SESSION = None

    def _search_kwargs(self, title: str) -> dict[str, object]:
        kwargs: dict[str, object] = {"query": title}
        if self.config.language:
            kwargs["language"] = self.config.language
        return kwargs

    def _result_list(self, response: object) -> list[dict]:
        if not isinstance(response, dict):
            return []

        results = response.get("results", [])
        if not isinstance(results, list):
            return []

        normalized: list[dict] = []
        for item in results:
            if isinstance(item, dict):
                normalized.append(dict(item))
            elif hasattr(item, "__dict__"):
                normalized.append(dict(item.__dict__))
        return normalized

    async def search_movie(self, title: str, year: int = None) -> list:
        """搜索电影"""
        if not self.config.api_key:
            return []
        try:
            response = await asyncio.to_thread(self.search_api.movie, **self._search_kwargs(title))
            results = self._result_list(response)

            if year:
                filtered = [
                    movie
                    for movie in results
                    if str(movie.get("release_date") or "").startswith(str(year))
                ]
                if filtered:
                    results = filtered

            return results
        except Exception as e:
            logger.error(f"TMDB movie search failed: {e}")
            return []

    async def search_tv(self, title: str, year: int = None) -> list:
        """搜索剧集"""
        if not self.config.api_key:
            return []
        try:
            response = await asyncio.to_thread(self.search_api.tv, **self._search_kwargs(title))
            results = self._result_list(response)

            if year:
                filtered = [
                    tv_item
                    for tv_item in results
                    if str(tv_item.get("first_air_date") or "").startswith(str(year))
                ]
                if filtered:
                    results = filtered

            return results
        except Exception as e:
            logger.error(f"TMDB TV search failed: {e}")
            return []

    async def get_movie_detail(self, tmdb_id: int) -> dict:
        """获取电影详情"""
        if not self.config.api_key:
            return {}
        try:
            movie_api = tmdb.Movies(tmdb_id)
            detail = await asyncio.to_thread(movie_api.info, language=self.config.language)
            return dict(detail) if isinstance(detail, dict) else {}
        except Exception as e:
            logger.error(f"TMDB movie detail failed: {e}")
            return {}

    async def get_tv_detail(self, tmdb_id: int) -> dict:
        """获取剧集详情"""
        if not self.config.api_key:
            return {}
        try:
            tv_api = tmdb.TV(tmdb_id)
            detail = await asyncio.to_thread(tv_api.info, language=self.config.language)
            return dict(detail) if isinstance(detail, dict) else {}
        except Exception as e:
            logger.error(f"TMDB tv detail failed: {e}")
            return {}


tmdb_client = TmdbClient()
