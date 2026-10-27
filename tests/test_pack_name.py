import pytest

from naming import make_pack_name


def test_pack_name_is_valid_and_has_bot_suffix():
    name = make_pack_name("Hello, world!", "demo_generator_bot", unique="a1b2c3")
    assert name.startswith("Emoji_")
    assert name.endswith("_by_demo_generator_bot")
    assert len(name) <= 64
    assert "__" not in name
    assert name == "Emoji_Hello_world_a1b2c3_by_demo_generator_bot"


def test_long_text_is_truncated_to_telegram_name_length():
    name = make_pack_name("A" * 20, "very_long_bot_username", unique="123abc")
    assert len(name) <= 64
    assert name.endswith("_by_very_long_bot_username")


def test_missing_bot_username_is_rejected():
    with pytest.raises(ValueError):
        make_pack_name("Hello", "")
