from fastapi.testclient import TestClient

from app.main import app, service


def _configure_disabled_service():
    service.status = "missing_mongodb_uri"
    service.reason = "MONGODB_URI is not configured."
    service.ready = False
    service.mode = "disabled"
    service.webhook_secret = ""


def test_liveness_and_readiness_probes(monkeypatch):
    async def fake_start():
        _configure_disabled_service()

    async def fake_stop():
        return None

    monkeypatch.setattr(service, "start", fake_start)
    monkeypatch.setattr(service, "stop", fake_stop)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.head("/healthz").status_code == 200
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "missing_mongodb_uri"


def test_webhook_requires_ready_service(monkeypatch):
    async def fake_start():
        _configure_disabled_service()

    async def fake_stop():
        return None

    monkeypatch.setattr(service, "start", fake_start)
    monkeypatch.setattr(service, "stop", fake_stop)
    with TestClient(app) as client:
        response = client.post("/telegram/webhook", json={"update_id": 1})
        assert response.status_code == 503
