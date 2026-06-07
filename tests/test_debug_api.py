from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.debug import router


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
            "stats": {
                "db_sync_rows": 3,
                "cleaned_records": 1,
                "deleted_strm_files": 1,
                "generated_strm_files": 2,
            },
            "error": "",
        }

    monkeypatch.setattr("app.api.debug.cloud115_full_sync_service.get_task", fake_get)

    client = TestClient(app)
    response = client.get("/api/v1/debug/cloud115/full-sync/task-123")

    assert response.status_code == 200
    assert response.json()["completed"] is True
    assert response.json()["stats"]["generated_strm_files"] == 2