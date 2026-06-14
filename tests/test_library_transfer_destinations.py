from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.library import router


def test_transfer_destinations_returns_cloud_strm_sync_dirs(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    monkeypatch.setattr(
        "app.config._config_instance",
        SimpleNamespace(
            cloud115=SimpleNamespace(
                sync_dirs=[
                    SimpleNamespace(dir_id="cid-115-a", name="115 STRM 扫描源目录"),
                    SimpleNamespace(dir_id="cid-115-b", name="115 动漫扫描源"),
                ]
            ),
            cloud123=SimpleNamespace(sync_dirs=[]),
        ),
    )

    response = TestClient(app).get("/api/v1/library/transfer_destinations?cloud_type=115")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "cloud_type": "115",
        "cloud_name": "115 网盘",
        "destinations": [
            {"dir_id": "cid-115-a", "name": "115 STRM 扫描源目录"},
            {"dir_id": "cid-115-b", "name": "115 动漫扫描源"},
        ],
    }
