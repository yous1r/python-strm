from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.core.tmdb.client as tmdb_client_module


class FakeSearch:
    movie_calls: list[dict] = []
    tv_calls: list[dict] = []

    def movie(self, **kwargs):
        self.movie_calls.append(kwargs)
        return {
            "results": [
                {"id": 1, "title": "Inception", "release_date": "2010-07-16"},
                {"id": 2, "title": "Inception Older", "release_date": "2009-01-01"},
            ]
        }

    def tv(self, **kwargs):
        self.tv_calls.append(kwargs)
        return {
            "results": [
                {"id": 3, "name": "Demo", "first_air_date": "2026-01-01"},
                {"id": 4, "name": "Demo Old", "first_air_date": "2025-01-01"},
            ]
        }


class FakeMovies:
    calls: list[dict] = []

    def __init__(self, tmdb_id):
        self.tmdb_id = tmdb_id

    def info(self, **kwargs):
        self.calls.append({"tmdb_id": self.tmdb_id, "kwargs": kwargs})
        return {"id": self.tmdb_id, "title": "Inception"}


class FakeTV:
    calls: list[dict] = []

    def __init__(self, tmdb_id):
        self.tmdb_id = tmdb_id

    def info(self, **kwargs):
        self.calls.append({"tmdb_id": self.tmdb_id, "kwargs": kwargs})
        return {"id": self.tmdb_id, "name": "Demo"}


class FakeSession:
    def __init__(self):
        self.proxies = {}


class FakeRequests:
    Session = FakeSession


class FakeTmdbModule:
    __name__ = "tmdbsimple"
    API_KEY = None
    REQUESTS_SESSION = "stale-session"
    Search = FakeSearch
    Movies = FakeMovies
    TV = FakeTV


def _reset_fakes():
    FakeSearch.movie_calls = []
    FakeSearch.tv_calls = []
    FakeMovies.calls = []
    FakeTV.calls = []
    FakeTmdbModule.API_KEY = None
    FakeTmdbModule.REQUESTS_SESSION = "stale-session"


def _fake_config(*, api_key="key", language="zh-CN", proxy=""):
    return SimpleNamespace(tmdb=SimpleNamespace(api_key=api_key, language=language, proxy=proxy))


def test_tmdb_client_imports_tmdbsimple_not_tmdbv3api():
    source = tmdb_client_module.__file__
    with open(source, "r", encoding="utf-8") as handle:
        content = handle.read()

    assert "tmdbv3api" not in content
    assert "tmdbsimple" in content


def test_docker_requirements_include_tmdbsimple_dependency():
    with open("requirements.txt", "r", encoding="utf-8") as handle:
        requirements = handle.read()

    assert "tmdbv3api" not in requirements
    assert "tmdbsimple" in requirements
    assert "requests" in requirements


@pytest.mark.asyncio
async def test_tmdb_client_search_uses_tmdbsimple_and_keeps_year_filtering():
    _reset_fakes()
    with (
        patch.object(tmdb_client_module, "tmdb", FakeTmdbModule, create=True),
        patch.object(tmdb_client_module, "requests", FakeRequests, create=True),
        patch.object(tmdb_client_module, "get_config", return_value=_fake_config()),
    ):
        client = tmdb_client_module.TmdbClient()
        movies = await client.search_movie("Inception", 2010)
        tvs = await client.search_tv("Demo", 2026)

    assert FakeTmdbModule.API_KEY == "key"
    assert movies == [{"id": 1, "title": "Inception", "release_date": "2010-07-16"}]
    assert tvs == [{"id": 3, "name": "Demo", "first_air_date": "2026-01-01"}]
    assert FakeSearch.movie_calls == [{"query": "Inception", "language": "zh-CN"}]
    assert FakeSearch.tv_calls == [{"query": "Demo", "language": "zh-CN"}]


@pytest.mark.asyncio
async def test_tmdb_client_details_use_tmdbsimple_info_with_language():
    _reset_fakes()
    with (
        patch.object(tmdb_client_module, "tmdb", FakeTmdbModule, create=True),
        patch.object(tmdb_client_module, "requests", FakeRequests, create=True),
        patch.object(tmdb_client_module, "get_config", return_value=_fake_config(language="en-US")),
    ):
        client = tmdb_client_module.TmdbClient()
        movie = await client.get_movie_detail(27205)
        tv = await client.get_tv_detail(1399)

    assert movie == {"id": 27205, "title": "Inception"}
    assert tv == {"id": 1399, "name": "Demo"}
    assert FakeMovies.calls == [{"tmdb_id": 27205, "kwargs": {"language": "en-US"}}]
    assert FakeTV.calls == [{"tmdb_id": 1399, "kwargs": {"language": "en-US"}}]


def test_tmdb_client_configures_tmdbsimple_session_proxy():
    _reset_fakes()
    with (
        patch.object(tmdb_client_module, "tmdb", FakeTmdbModule, create=True),
        patch.object(tmdb_client_module, "requests", FakeRequests, create=True),
        patch.object(
            tmdb_client_module,
            "get_config",
            return_value=_fake_config(proxy="http://127.0.0.1:7890"),
        ),
    ):
        tmdb_client_module.TmdbClient()

    assert isinstance(FakeTmdbModule.REQUESTS_SESSION, FakeSession)
    assert FakeTmdbModule.REQUESTS_SESSION.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_tmdb_client_clears_tmdbsimple_session_when_proxy_disabled():
    _reset_fakes()
    with (
        patch.object(tmdb_client_module, "tmdb", FakeTmdbModule, create=True),
        patch.object(tmdb_client_module, "requests", FakeRequests, create=True),
        patch.object(tmdb_client_module, "get_config", return_value=_fake_config(proxy="")),
    ):
        tmdb_client_module.TmdbClient()

    assert FakeTmdbModule.REQUESTS_SESSION is None
