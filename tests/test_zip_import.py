import io
import json
import zipfile

import pytest

from app.telegram.zip_import import (
    MAX_ZIP_EXPANDED_BYTES,
    PackImportError,
    extract_zip_member,
    parse_pack_archive,
)


def make_zip(entries, manifest=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
    return buffer.getvalue()


def test_parse_manifest_archive_with_animation_types_and_metadata():
    data = make_zip(
        [
            ("001_1f381.tgs", b"synthetic-tgs"),
            ("002_1f3c0.webm", b"synthetic-webm"),
            ("003_sticker.webp", b"synthetic-webp"),
            ("notes.txt", b"ignore me"),
        ],
        {
            "pack_title": "Playfina_emoji",
            "pack_short_name": "Playfina_emoji_animated",
            "pack_kind": "custom_emoji",
            "files": [
                {"filename": "001_1f381.tgs", "emoji": "🎁", "width": 512, "height": 512},
                {"filename": "002_1f3c0.webm", "emoji": "🏀", "width": 512, "height": 512},
            ],
        },
    )
    parsed = parse_pack_archive(data, "fallback.zip")
    assert parsed["title"] == "Playfina_emoji"
    assert parsed["short_name"] == "Playfina_emoji_animated"
    assert parsed["kind"] == "custom_emoji"
    assert parsed["expanded_bytes"] == len(b"synthetic-tgs") + len(b"synthetic-webm") + len(b"synthetic-webp")
    assert [item["source_member"] for item in parsed["items"]] == [
        "001_1f381.tgs",
        "002_1f3c0.webm",
        "003_sticker.webp",
    ]
    assert parsed["items"][0]["emoji"] == "🎁"
    assert parsed["items"][0]["is_animated"] is True
    assert parsed["items"][1]["is_video"] is True
    assert parsed["items"][2]["is_animated"] is False


def test_parse_archive_without_manifest_uses_safe_defaults():
    data = make_zip([("assets/heart.tgs", b"tgs-data")])
    parsed = parse_pack_archive(data, "my_templates.zip")
    assert parsed["title"] == "my_templates.zip"
    assert parsed["short_name"] == ""
    assert parsed["kind"] == "stickers"
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["source_member"] == "assets/heart.tgs"


def test_extract_zip_member_returns_original_bytes_unchanged():
    original = b"original-tgs-bytes\x00\xff"
    data = make_zip([("nested/001_test.tgs", original)])
    assert extract_zip_member(data, "nested/001_test.tgs") == original


@pytest.mark.parametrize("path", ["../escape.tgs", "/absolute.tgs", "a\\b.tgs", "C:/escape.tgs"])
def test_parse_rejects_unsafe_supported_member_paths(path):
    data = make_zip([(path, b"animation")])
    with pytest.raises(PackImportError, match="path"):
        parse_pack_archive(data)


def test_extract_rejects_path_traversal_and_missing_member():
    data = make_zip([("valid.tgs", b"animation")])
    with pytest.raises(PackImportError, match="path"):
        extract_zip_member(data, "../valid.tgs")
    with pytest.raises(PackImportError, match="missing or ambiguous"):
        extract_zip_member(data, "missing.tgs")


def test_parse_rejects_empty_archive_and_corrupt_zip():
    with pytest.raises(PackImportError, match="No .TGS"):
        parse_pack_archive(make_zip([("readme.txt", b"only text")]))
    with pytest.raises(PackImportError, match="valid ZIP"):
        parse_pack_archive(b"not a zip")


def test_parse_rejects_oversized_expanded_archive():
    # Use ZIP_STORED and a sparse-ish large entry so the preflight size check
    # rejects it without reading/extracting the animation payload.
    payload = b"x" * (MAX_ZIP_EXPANDED_BYTES + 1)
    data = make_zip([("too_big.tgs", payload)])
    with pytest.raises(PackImportError, match="45 MiB"):
        parse_pack_archive(data)
