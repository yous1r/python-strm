from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router


def test_bark_notify_test_reports_send_failure(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_send_message(content, title):
        return False

    monkeypatch.setattr("app.core.notify.bark.notifier.send_message", fake_send_message)

    response = TestClient(app).post("/system/test-notify/bark")

    assert response.status_code == 502
    assert "Bark 测试通知发送失败" in response.json()["detail"]
