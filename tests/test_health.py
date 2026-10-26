from fastapi.testclient import TestClient

from main import app


def test_liveness_remains_available_before_secrets_are_added(monkeypatch):
    for key in ("BOT_TOKEN", "MONGODB_URI", "ADMIN_IDS"):
        monkeypatch.delenv(key, raising=False)

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["service"] == "animated-text-creator"


def test_readiness_requires_bot_configuration(monkeypatch):
    for key in ("BOT_TOKEN", "MONGODB_URI", "ADMIN_IDS"):
        monkeypatch.delenv(key, raising=False)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
