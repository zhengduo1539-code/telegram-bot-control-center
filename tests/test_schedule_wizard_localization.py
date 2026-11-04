"""Regression tests for complete 20-language Schedule Post localization."""

from localization import LANGUAGES
from schedule_wizard_localization import (
    _MESSAGES,
    schedule_wizard_weekdays,
    translate_schedule_wizard,
)
from handlers import schedule_wizard as wizard


def test_schedule_wizard_catalog_is_complete_for_all_supported_languages():
    assert len(LANGUAGES) == 20
    assert set(_MESSAGES) == set(LANGUAGES)

    expected_keys = set(_MESSAGES["en"])
    for language, catalog in _MESSAGES.items():
        assert set(catalog) == expected_keys, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values()), language
        assert all(value != key for key, value in catalog.items()), language


def test_each_language_has_seven_localized_weekday_labels():
    for language in LANGUAGES:
        weekdays = schedule_wizard_weekdays(language)
        assert len(weekdays) == 7, language
        assert all(isinstance(day, str) and day for day in weekdays), language
        assert len(set(weekdays)) == 7, language


def test_schedule_wizard_translations_interpolate_dynamic_values():
    for language in LANGUAGES:
        success = translate_schedule_wizard(language, "save_success", post_id="12345")
        button = translate_schedule_wizard(language, "button_added", count=3)
        timezone = translate_schedule_wizard(
            language, "timezone_selected", timezone="Asia/Yangon"
        )
        manual_id = translate_schedule_wizard(
            language, "manual_id_prompt", example="-1001234567890"
        )

        assert "12345" in success, language
        assert "3" in button, language
        assert "Asia/Yangon" in timezone, language
        assert "-1001234567890" in manual_id, language


def test_unknown_or_missing_locale_falls_back_to_english():
    assert translate_schedule_wizard("xx", "save_success", post_id="7") == (
        translate_schedule_wizard("en", "save_success", post_id="7")
    )
    assert translate_schedule_wizard(None, "no_groups") == translate_schedule_wizard(
        "en", "no_groups"
    )


def test_schedule_post_keyboard_labels_follow_the_selected_language():
    for language in LANGUAGES:
        recurrence = [
            button.text
            for row in wizard._recurrence_keyboard(language).inline_keyboard
            for button in row
        ]
        content = [
            button.text
            for row in wizard._content_step_keyboard(True, language).inline_keyboard
            for button in row
        ]
        post_buttons = [
            button.text
            for row in wizard._inline_btns_keyboard(0, language).inline_keyboard
            for button in row
        ]
        weekdays = [
            button.text
            for row in wizard._dow_keyboard(set(), language).inline_keyboard[:2]
            for button in row
        ]

        assert recurrence[0] == __import__("localization").translate(
            language, "recurrence_interval"
        )
        assert content[0] == translate_schedule_wizard(
            language, "keep_content_button"
        )
        assert post_buttons[0] == translate_schedule_wizard(language, "add_button")
        assert all(day.endswith(name) for day, name in zip(weekdays, schedule_wizard_weekdays(language)))


def test_final_review_uses_localized_labels_and_preserves_schedule_values():
    draft = {
        "chat_id": -1001234567890,
        "chat_title": "Publisher Group",
        "content_text": "A scheduled announcement",
        "recurrence_type": "once",
        "timezone": "Asia/Yangon",
        "first_run": None,
        "max_runs": 1,
        "time_window_enabled": True,
        "tw_start": "22:00",
        "tw_end": "06:00",
        "auto_delete": False,
        "self_destruct": False,
        "auto_pin": True,
        "ap_hours": None,
        "inline_buttons": [],
    }

    summary = wizard._build_summary(draft, "my")
    assert "Publisher Group" in summary
    assert "22:00" in summary and "06:00" in summary
    assert translate_schedule_wizard("my", "destination_label") in summary
    assert translate_schedule_wizard("my", "review_confirm") in summary
    assert "Final Review" not in summary
