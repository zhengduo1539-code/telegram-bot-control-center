from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-long-enough")

    async def fake_proxy(bot_id: str, method: str = "GET", endpoint: str = "features", payload=None):
        main.get_bot(bot_id)
        return {"bot": bot_id, "method": method, "endpoint": endpoint, "payload": payload}

    monkeypatch.setattr(main, "proxy_management", fake_proxy)
    return TestClient(main.app)


def test_management_api_requires_dashboard_auth(client: TestClient) -> None:
    response = client.get("/api/manage/z28/features")
    assert response.status_code == 401


def test_management_features_proxy_is_authenticated(client: TestClient) -> None:
    response = client.get("/api/manage/z28/features", auth=("owner", "test-password-long-enough"))
    assert response.status_code == 200
    assert response.json()["bot"] == "z28"
    assert response.json()["endpoint"] == "features"


def test_activity_limit_writes_are_authenticated_and_forwarded(client: TestClient) -> None:
    response = client.put(
        "/api/manage/m58/activity-limits",
        auth=("owner", "test-password-long-enough"),
        json={"kind": "wc", "minutes": 10},
    )
    assert response.status_code == 200
    assert response.json()["method"] == "PUT"
    assert response.json()["payload"] == {"kind": "wc", "minutes": 10}


def test_invalid_bot_is_rejected_before_proxy(client: TestClient) -> None:
    response = client.get("/api/manage/not-a-bot/features", auth=("owner", "test-password-long-enough"))
    assert response.status_code == 404
