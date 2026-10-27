from app.config import Settings


def test_defaults_keep_database_disabled(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_RENDERS", raising=False)
    settings = Settings.from_env()
    assert settings.bot_token == ""
    assert settings.database_path is None
    assert settings.max_concurrent_renders == 2


def test_render_concurrency_is_bounded(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RENDERS", "100")
    assert Settings.from_env().max_concurrent_renders == 4
