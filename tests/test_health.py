"""Regression tests for liveness and readiness endpoints."""
from keep_alive import app, set_readiness


def test_healthz_returns_http_200():
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_health_reports_readiness_state():
    client = app.test_client()
    set_readiness(database=False, scheduler=False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["ready"] is False

    set_readiness(database=True, scheduler=True)
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.get_json()["ready"] is True
    finally:
        set_readiness(database=False, scheduler=False)
