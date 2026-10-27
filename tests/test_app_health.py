from fastapi.testclient import TestClient

from app.main import app, service


def test_health_endpoints_work_without_bot_token(monkeypatch):
    monkeypatch.setattr(service, "token", "")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.head("/healthz").status_code == 200
        assert client.get("/health").status_code == 503
