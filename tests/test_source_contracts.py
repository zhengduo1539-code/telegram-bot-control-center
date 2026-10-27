from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_api_supports_custom_emoji_and_regular_animated_stickers():
    source = (ROOT / "app" / "telegram" / "sticker_api.py").read_text(encoding="utf-8")
    assert 'format="animated"' in source
    assert 'sticker_type=pack_type' in source
    assert 'link_prefix = "addemoji" if pack_type == "custom_emoji" else "addstickers"' in source


def test_bot_has_no_payment_quota_or_database_runtime_dependencies():
    paths = [
        ROOT / "app" / "bot" / "service.py",
        ROOT / "app" / "config.py",
        ROOT / "app" / "telegram" / "sticker_api.py",
        ROOT / "requirements.txt",
        ROOT / ".env.example",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    for forbidden in (
        "precheckoutqueryhandler", "successful_payment", "labeledprice",
        "send_invoice", "pro_price_stars", "daily_limit", "usage_store",
        "mongodb_uri", "pymongo",
    ):
        assert forbidden not in combined


def test_render_blueprint_keeps_existing_service_identity():
    render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "name: telegram-bot-control-center" in render_yaml
    assert "healthCheckPath: /healthz" in render_yaml


def test_render_entry_point_and_health_endpoints_remain_compatible():
    root_main = (ROOT / "main.py").read_text(encoding="utf-8")
    app_main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "from app.main import app" in root_main
    assert '@app.api_route("/health", methods=["GET", "HEAD"])' in app_main
    assert '@app.api_route("/healthz", methods=["GET", "HEAD"])' in app_main
