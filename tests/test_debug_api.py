from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.debug import router
from app.api.web import router as web_router


def test_start_cloud115_full_sync_returns_task_id(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_start(source="debug"):
        return {
            "task_id": "task-123",
            "status": "running",
            "current_stage": "queued",
            "message": "started",
        }

    monkeypatch.setattr("app.api.debug.cloud115_full_sync_service.start_full_sync", fake_start)

    client = TestClient(app)
    response = client.post("/api/v1/debug/cloud115/full-sync")

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-123"
    assert response.json()["success"] is True


def test_get_cloud115_full_sync_returns_stats(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_get(task_id):
        return {
            "task_id": task_id,
            "status": "completed",
            "current_stage": "completed",
            "started_at": "2026-06-07T20:00:00",
            "finished_at": "2026-06-07T20:00:05",
            "db_sync_results": [{"dir_name": "影视", "count": 3}],
            "manifest_results": [{"dir_name": "影视", "cleaned_records": 1, "deleted_strm_files": 1}],
            "strm_results": [{"dir_name": "影视", "generated_count": 2}],
            "media_link_results": [{"media_server_name": "emby-1", "scanned": 10, "linked": 2, "skipped": 8, "failed": 0}],
            "stats": {
                "db_sync_rows": 3,
                "cleaned_records": 1,
                "deleted_strm_files": 1,
                "generated_strm_files": 2,
                "media_links_scanned": 10,
                "media_links_linked": 2,
                "media_links_skipped": 8,
                "media_links_failed": 0,
            },
            "skip_reason": "",
            "error": "",
        }

    monkeypatch.setattr("app.api.debug.cloud115_full_sync_service.get_task", fake_get)

    client = TestClient(app)
    response = client.get("/api/v1/debug/cloud115/full-sync/task-123")

    assert response.status_code == 200
    assert response.json()["completed"] is True
    assert response.json()["stats"]["generated_strm_files"] == 2
    assert response.json()["stats"]["media_links_linked"] == 2


def test_trigger_telegram_latest_transfer_returns_background_sync_result(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_run():
        return {
            "status": "success",
            "resource_count": 2,
            "successful_transfers": 1,
            "full_sync": {"status": "skipped", "reason": "db_sync_already_scheduled_soon"},
        }

    monkeypatch.setattr("app.api.debug.telegram_background_sync_service.run_scheduled_sync", fake_run)

    client = TestClient(app)
    response = client.post("/api/v1/debug/telegram/latest-transfer")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["resource_count"] == 2
    assert response.json()["full_sync"]["reason"] == "db_sync_already_scheduled_soon"


def test_debug_page_contains_telegram_latest_transfer_button():
    app = FastAPI()
    app.include_router(web_router)

    client = TestClient(app)
    response = client.get("/debug")

    assert response.status_code == 200
    assert "Telegram 最新消息转存" in response.text
    assert "step10Btn" in response.text


def test_monitor_page_contains_telegram_tabs_and_history_sync_fields():
    app = FastAPI()
    app.include_router(web_router)

    client = TestClient(app)
    response = client.get("/monitor")

    assert response.status_code == 200
    assert "实时监听" in response.text
    assert "历史同步" in response.text
    assert "启动编排" in response.text
    assert "tab-realtime" in response.text
    assert "tab-history" in response.text
    assert "tab-startup" in response.text
    assert "mon_tg_history_mode" in response.text
    assert "mon_tg_history_scheduled_enabled" in response.text
    assert "mon_startup_pipeline_enabled" in response.text