from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_api_uses_animated_custom_emoji_sets():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "sticker_type=Sticker.CUSTOM_EMOJI" in source
    assert 'format="animated"' in source
    assert "https://t.me/addemoji/" in source
    assert "https://t.me/addstickers/" not in source


def test_bot_has_no_payment_quota_or_database_runtime_dependencies():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8").lower()
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8").lower()
    for forbidden in (
        "precheckoutqueryhandler", "successful_payment", "labeledprice",
        "send_invoice", "pro_price_stars", "daily_limit", "usage_store",
        "mongodb_uri", "pymongo",
    ):
        assert forbidden not in main_source
        assert forbidden not in requirements
        assert forbidden not in env_example


def test_render_blueprint_keeps_existing_service_identity():
    render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "name: telegram-bot-control-center" in render_yaml
    assert "healthCheckPath: /healthz" in render_yaml


def test_render_health_endpoints_accept_get_and_head():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '@app.api_route("/health", methods=["GET", "HEAD"])' in source
    assert '@app.api_route("/healthz", methods=["GET", "HEAD"])' in source
