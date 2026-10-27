from main import health, healthz


def test_healthz_is_liveness_endpoint():
    response = __import__("asyncio").run(healthz())
    assert response.status_code == 200
    assert b'"status":"ok"' in response.body


def test_health_endpoint_reports_bot_readiness_state():
    response = __import__("asyncio").run(health())
    assert response.status_code in (200, 503)
    assert b'"telegram_ready"' in response.body
