import sqlite3

from app.storage.pack_registry import PackRegistry


def test_registry_is_disabled_by_default_and_writes_nothing():
    registry = PackRegistry()
    registry.record(user_id=7, pack_name="x", pack_type="regular", display_text="Hello")
    assert registry.enabled is False


def test_registry_stores_metadata_only_and_ignores_duplicates(tmp_path):
    database = tmp_path / "data" / "packs.sqlite3"
    registry = PackRegistry(str(database))
    assert registry.enabled is True

    registry.record(user_id=123, pack_name="Emoji_Hello_123_by_bot", pack_type="custom_emoji", display_text="Hello")
    registry.record(user_id=123, pack_name="Emoji_Hello_123_by_bot", pack_type="custom_emoji", display_text="Hello")

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT user_id, pack_name, pack_type, display_text FROM pack_registry"
        ).fetchall()
    assert rows == [(123, "Emoji_Hello_123_by_bot", "custom_emoji", "Hello")]
