from app.config import Settings


def test_settings_defaults_and_bounds(monkeypatch):
    for key in ("BOT_TOKEN", "MONGODB_URI", "MONGODB_DATABASE", "PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "WEBHOOK_SECRET", "MAX_DOWNLOAD_BYTES"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env()
    assert settings.bot_token == ""
    assert settings.mongodb_uri == ""
    assert settings.mongodb_database == "emoji_rebuilder"
    assert settings.max_download_bytes == 20 * 1024 * 1024
    assert settings.public_base_url == ""


def test_render_url_is_used_as_public_base(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com/")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.public_base_url == "https://example.onrender.com"


def test_explicit_public_url_takes_precedence(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://public.example/")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://service.onrender.com")
    assert Settings.from_env().public_base_url == "https://public.example"


def test_invalid_webhook_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "has spaces")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "WEBHOOK_SECRET" in str(exc)
    else:
        raise AssertionError("Invalid webhook secret should be rejected.")
