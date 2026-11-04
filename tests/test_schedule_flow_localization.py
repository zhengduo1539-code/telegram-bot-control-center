"""Coverage for the complete 20-language Schedule New Post workflow."""
from datetime import datetime, timedelta, timezone

from handlers import schedule_wizard as wizard
from localization import LANGUAGES, translate
from schedule_flow_localization import (
    FLOW_KEYS,
    FLOW_TRANSLATIONS,
    translate_schedule_flow,
)


def test_schedule_flow_catalog_covers_every_locale_and_every_key():
    assert set(FLOW_TRANSLATIONS) == set(LANGUAGES)
    assert len(FLOW_KEYS) == len(set(FLOW_KEYS))
    for locale in LANGUAGES:
        catalog = FLOW_TRANSLATIONS[locale]
        assert set(catalog) == set(FLOW_KEYS), locale
        assert all(isinstance(catalog[key], str) and catalog[key].strip() for key in FLOW_KEYS), locale


def test_all_schedule_translations_format_without_missing_placeholders():
    for locale, catalog in FLOW_TRANSLATIONS.items():
        for key, template in catalog.items():
            values = {
                placeholder: "TEST"
                for placeholder in __import__("re").findall(r"\{([a-z_]+)\}", template)
            }
            rendered = translate_schedule_flow(locale, key, **values)
            assert rendered and "{" not in rendered and "}" not in rendered, (locale, key)


def test_unknown_locale_falls_back_to_english_without_losing_keys():
    for key in FLOW_KEYS:
        assert translate_schedule_flow("unsupported", key) == translate_schedule_flow("en", key)


def test_all_twenty_locales_render_translated_schedule_controls():
    for locale in LANGUAGES:
        first_run = [
            button.text
            for row in wizard._first_run_keyboard("back:test", locale).inline_keyboard
            for button in row
        ]
        assert wizard.flow_tr(locale, "suggest_time") in first_run

        inline_buttons = [
            button.text
            for row in wizard._inline_btns_keyboard(0, locale).inline_keyboard
            for button in row
        ]
        assert wizard.flow_tr(locale, "add_button") in inline_buttons
        assert wizard.flow_tr(locale, "skip") in inline_buttons

        delivery = [
            button.text
            for row in wizard._max_runs_keyboard(language=locale).inline_keyboard
            for button in row
        ]
        assert wizard.flow_tr(locale, "unlimited") in " ".join(delivery)

        advanced = [
            button.text
            for row in wizard._advanced_keyboard(False, False, False, False, locale).inline_keyboard
            for button in row
        ]
        assert wizard.flow_tr(locale, "auto_delete_label") in " ".join(advanced)
        assert wizard.flow_tr(locale, "auto_pin_label") in " ".join(advanced)

        pin = [
            button.text
            for row in wizard._auto_pin_keyboard(locale).inline_keyboard
            for button in row
        ]
        assert wizard.flow_tr(locale, "pin_forever") in pin

        weekdays = wizard._dow_keyboard(set(), locale)
        weekday_labels = wizard.flow_tr(locale, "weekdays").split(", ")
        rendered_days = [
            button.text
            for row in weekdays.inline_keyboard
            for button in row
        ]
        assert all(any(label in button for button in rendered_days) for label in weekday_labels)


def test_destination_and_recurrence_controls_use_selected_locale():
    groups = [
        {"chat_id": -100123, "title": "Announcements", "chat_type": "supergroup"},
        {"chat_id": -100456, "title": "News", "chat_type": "channel"},
    ]
    for locale in LANGUAGES:
        destination = wizard._chat_list_keyboard(groups, 0, "ScheduleBot", locale)
        labels = [button.text for row in destination.inline_keyboard for button in row]
        assert translate(locale, "manual_chat_id") in labels
        assert translate(locale, "cancel") in labels

        empty = wizard._no_groups_keyboard("ScheduleBot", locale)
        empty_labels = [button.text for row in empty.inline_keyboard for button in row]
        assert wizard.flow_tr(locale, "add_group") in empty_labels

        recurrence = wizard._recurrence_keyboard(locale)
        recurrence_labels = [button.text for row in recurrence.inline_keyboard for button in row]
        assert translate(locale, "recurrence_interval") in recurrence_labels
        assert translate(locale, "recurrence_days") in recurrence_labels
        assert translate(locale, "recurrence_once") in recurrence_labels


def test_auto_delete_duration_accepts_localized_minute_units():
    samples = {
        "en": "5 minutes",
        "my": "5 မိနစ်",
        "zh": "5 分钟",
        "es": "5 minutos",
        "hi": "5 मिनट",
        "ar": "5 دقائق",
        "pt": "5 minutos",
        "fr": "5 minutes",
        "ru": "5 минут",
        "bn": "5 মিনিট",
        "id": "5 menit",
        "ja": "5 分",
        "ko": "5 분",
        "th": "5 นาที",
        "vi": "5 phút",
        "tr": "5 dakika",
        "de": "5 Minuten",
        "it": "5 minuti",
        "ur": "5 منٹ",
        "sw": "5 dakika",
    }
    assert set(samples) == set(LANGUAGES)
    for locale, example in samples.items():
        assert wizard._parse_auto_delete_duration(example, locale) == 5 / 60, locale
    assert wizard._parse_auto_delete_duration("4 minutes", "en") is None
    assert wizard._parse_auto_delete_duration("2 hours", "en") == 2


def test_final_schedule_review_uses_selected_locale_and_keeps_markdown_escaping():
    draft = {
        "chat_id": -1001234567890,
        "chat_title": "News_*[Group]",
        "content_text": "Breaking_*[news]",
        "recurrence_type": "once",
        "timezone": "Asia/Yangon",
        "first_run": datetime.now(timezone.utc) + timedelta(hours=2),
        "max_runs": 1,
        "time_window_enabled": False,
        "auto_delete": False,
        "self_destruct": False,
        "auto_pin": False,
        "inline_buttons": [[{"text": "Open_*[link]", "url": "https://example.com"}]],
    }
    english = wizard._build_summary(draft, "en")
    assert "1 send" in english
    assert "Breaking\\_\\*\\[news]" in english
    assert "News\\_\\*\\[Group]" in english
    assert "Open\\_\\*\\[link]" in english

    burmese = wizard._build_summary(draft, "my")
    assert wizard.flow_tr("my", "review_template").splitlines()[1] in burmese
    assert wizard.flow_tr("my", "one_send") in burmese
