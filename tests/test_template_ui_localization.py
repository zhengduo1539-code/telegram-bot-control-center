"""Regression tests for localized template UX and empty-state presentation."""
from handlers import queue_manager
from handlers.templates import _build_template_preview
from localization import LANGUAGES
from media_pool_localization import translate_media_pool
from queue_manager_localization import translate_queue_manager
from template_ui_localization import (
    TRANSLATIONS,
    _KEYS,
    _placeholder_fields,
    translate_template_ui,
)


def test_template_ux_catalog_covers_all_twenty_languages_and_placeholders():
    assert set(TRANSLATIONS) == set(LANGUAGES)
    expected_keys = set(_KEYS)
    assert len(_KEYS) == len(expected_keys)
    expected_placeholders = {
        key: _placeholder_fields(TRANSLATIONS["en"][key])
        for key in _KEYS
    }
    for language, catalog in TRANSLATIONS.items():
        assert set(catalog) == expected_keys, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values())
        for key in _KEYS:
            assert _placeholder_fields(catalog[key]) == expected_placeholders[key], (language, key)


def test_template_preview_escapes_user_markdown_and_localizes():
    preview = _build_template_preview(
        {
            "name": "Weekly *Update*",
            "content": {
                "media_type": "photo",
                "text": "Hello *everyone* [click](https://example.com)",
                "inline_keyboard": [[{"text": "Open *details*"}]],
            },
        },
        "my",
    )
    assert "Weekly \\*Update\\*" in preview
    assert "Hello \\*everyone\\*" in preview
    assert "Open \\*details\\*" in preview
    assert "Template:" in preview
    assert "Media အမျိုးအစား" in preview
    assert "To use this template" not in preview


def test_template_preview_reports_media_only_content():
    preview = _build_template_preview(
        {"name": "Voice note", "content": {"media_type": "voice", "media_file_id": "file-1"}},
        "en",
    )
    assert "🎤" in preview
    assert "No caption or text saved" in preview


def test_queue_and_media_pool_empty_states_are_available_in_all_locales():
    for language in LANGUAGES:
        assert translate_queue_manager(language, "view_empty").strip()
        assert translate_media_pool(language, "overview_empty").strip()
        assert translate_media_pool(language, "manage_empty").strip()
        if language != "en":
            assert "No Queues Yet" not in translate_queue_manager(language, "view_empty")
            assert "No content pools yet" not in translate_media_pool(language, "overview_empty")


def test_template_picker_preview_and_keyboard_use_selected_locale():
    template = {
        "_id": "0123456789abcdef01234567",
        "name": "Daily *Update*",
        "content": {
            "media_type": "photo",
            "media_file_id": "file-id",
            "text": "Hello *world*",
        },
    }
    preview = queue_manager._user_template_preview([template], 0, "queue", "my")
    keyboard = queue_manager._user_template_keyboard(
        [template], 0, "qm:template:page:", "qm:template:use:",
        "qm:back:content", "my",
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "အသုံးပြုသူ၏ Template" in preview
    assert "Daily \\*Update\\*" in preview
    assert "Hello \\*world\\*" in preview
    assert translate_template_ui("my", "use_template") in labels
    assert "✅ Use this Template" not in labels


def test_template_ui_interpolation_is_resilient():
    text = translate_template_ui("my", "user_template_title", current=2, total=4)
    assert "2/4" in text
    assert "{current}" not in text
