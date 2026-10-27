from app.telegram.links import parse_pack_link


def test_parse_custom_emoji_pack_link():
    link = parse_pack_link("https://t.me/addemoji/HappyPack_01?startapp=ignored")
    assert link is not None
    assert link.short_name == "HappyPack_01"
    assert link.kind == "custom_emoji"
    assert link.source_url == "https://t.me/addemoji/HappyPack_01"


def test_parse_regular_sticker_pack_link_without_scheme():
    link = parse_pack_link("t.me/addstickers/AnimatedPack")
    assert link is not None
    assert link.short_name == "AnimatedPack"
    assert link.kind == "stickers"


def test_rejects_arbitrary_hosts_and_invalid_paths():
    assert parse_pack_link("https://example.com/addemoji/SomePack") is None
    assert parse_pack_link("https://t.me/addemoji/../../secrets") is None
    assert parse_pack_link("https://t.me/some/path") is None
    assert parse_pack_link("https://t.me/addemoji/") is None
    assert parse_pack_link("javascript://t.me/addemoji/Pack") is None
