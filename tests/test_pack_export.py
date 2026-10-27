import io
import json
import zipfile

import pytest

from app.telegram.pack_export import (
    MAX_PACK_EXPORT_BYTES,
    PackExportError,
    PackZipBuilder,
    pack_archive_filename,
    pack_item_filename,
)


def test_pack_item_filename_uses_safe_indexed_names_and_detected_format():
    assert pack_item_filename(1, {"emoji": "🎁", "is_animated": True}) == "001_1f381.tgs"
    assert pack_item_filename(2, {"emoji": "🏀", "is_video": True}) == "002_1f3c0.webm"
    assert pack_item_filename(3, {"emoji": "", "is_animated": False, "is_video": False}) == "003_sticker.webp"
    assert "/" not in pack_item_filename(4, {"emoji": "../../", "is_video": True})


def test_archive_filename_sanitizes_pack_title():
    assert pack_archive_filename("Playfina emoji / pack!") == "Playfina_emoji_pack_all_files.zip"
    assert pack_archive_filename("../../") == "telegram_emoji_pack_all_files.zip"


def test_pack_zip_builder_exports_original_bytes_and_manifest():
    original_a = b"original-tgs-bytes\x00"
    original_b = b"original-webm-bytes\x01"
    builder = PackZipBuilder("Playfina emoji", "Playfina_emoji_animated", "custom_emoji")
    builder.add(1, {"emoji": "🎁", "is_animated": True, "width": 512, "height": 512}, original_a)
    builder.add(2, {"emoji": "🏀", "is_video": True, "width": 512, "height": 512}, original_b)
    data = builder.finish()

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["001_1f381.tgs", "002_1f3c0.webm", "manifest.json"]
        assert archive.read("001_1f381.tgs") == original_a
        assert archive.read("002_1f3c0.webm") == original_b
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["pack_title"] == "Playfina emoji"
        assert manifest["source_url"] == "https://t.me/addemoji/Playfina_emoji_animated"
        assert manifest["item_count"] == 2
        assert manifest["files"][0]["format"] == "TGS"
        assert manifest["files"][1]["format"] == "WEBM"


def test_custom_selection_does_not_create_fake_pack_url():
    builder = PackZipBuilder("Selected emojis", "custom-emoji", "custom_emoji")
    builder.add(1, {"emoji": "🎁"}, b"original")
    data = builder.finish()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["source_url"] == ""


def test_pack_zip_builder_rejects_aggregate_size_over_limit():
    builder = PackZipBuilder("big", "big_pack", "stickers")
    with pytest.raises(PackExportError, match="combined source files exceed"):
        builder.add(1, {"is_animated": True}, b"x" * (MAX_PACK_EXPORT_BYTES + 1))
    builder.abort()


def test_pack_zip_builder_rejects_empty_items_and_invalid_index():
    builder = PackZipBuilder("empty", "empty", "stickers")
    with pytest.raises(PackExportError, match="empty file"):
        builder.add(1, {"emoji": "🫥"}, b"")
    with pytest.raises(PackExportError, match="unsupported number"):
        pack_item_filename(201, {})
    builder.abort()


def test_pack_zip_builder_does_not_allow_finish_twice():
    builder = PackZipBuilder("once", "once", "stickers")
    builder.add(1, {}, b"one")
    builder.finish()
    with pytest.raises(PackExportError, match="already closed"):
        builder.finish()
