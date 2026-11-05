"""Regression tests proving the active bot UI follows all 20 saved locales."""
import re

from telegram import InlineKeyboardMarkup

from handlers import base, owner_panel, schedule_post_flow as composer
from handlers.permissions import _message as permission_message
from language_ui_localization import translate_language_ui
from localization import LANGUAGES, translate
from my_posts_localization import CATALOG as MY_POSTS_CATALOG, translate_my_posts
from owner_panel_localization import CATALOG as OWNER_CATALOG, translate_owner_panel
from post_composer_localization import CATALOG as COMPOSER_CATALOG, translate_post_composer
from schedule_flow_localization import FLOW_KEYS, FLOW_TRANSLATIONS, translate_schedule_flow
from system_localization import CATALOG as SYSTEM_CATALOG, command_descriptions, translate_system


def _assert_complete_catalog(catalog, locales):
    assert set(catalog) == set(locales)
    for locale, translations in catalog.items():
        assert translations
        for key, template in translations.items():
            assert isinstance(template, str) and template.strip(), (locale, key)
            placeholders = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template))
            rendered = template.format(**{placeholder: "TEST" for placeholder in placeholders})
            assert rendered.strip(), (locale, key)


def test_every_primary_ui_catalog_covers_all_20_languages():
    _assert_complete_catalog(COMPOSER_CATALOG, LANGUAGES)
    _assert_complete_catalog(MY_POSTS_CATALOG, LANGUAGES)
    _assert_complete_catalog(OWNER_CATALOG, LANGUAGES)
    _assert_complete_catalog(SYSTEM_CATALOG, LANGUAGES)
    _assert_complete_catalog(FLOW_TRANSLATIONS, LANGUAGES)
    assert set(translate_language_ui(code, "private_only") for code in LANGUAGES)
    for locale in LANGUAGES:
        assert len(command_descriptions(locale)) == 4


def test_active_schedule_post_home_and_section_keyboards_use_selected_locale():
    for locale in LANGUAGES:
        draft = {"language": locale, "inline_buttons": [], "days_of_week": set()}
        home = composer._home_keyboard(draft)
        labels = [button.text for row in home.inline_keyboard for button in row]
        assert translate_post_composer(locale, "section_content") in labels[0]
        assert translate_post_composer(locale, "section_destination") in labels[1]
        assert translate_post_composer(locale, "section_schedule") in labels[2]
        assert translate_post_composer(locale, "section_rules") in " ".join(labels)
        assert translate_post_composer(locale, "section_buttons") in " ".join(labels)
        assert translate_post_composer(locale, "preview") in labels
        assert translate(locale, "cancel") in " ".join(labels)

        schedule = composer._schedule_keyboard(draft)
        schedule_labels = [button.text for row in schedule.inline_keyboard for button in row]
        assert translate_post_composer(locale, "mode_once") in schedule_labels
        assert translate_post_composer(locale, "mode_interval") in schedule_labels
        assert translate_post_composer(locale, "mode_weekly") in schedule_labels
        assert translate_post_composer(locale, "first_publish") in schedule_labels
        assert translate_post_composer(locale, "timezone") in schedule_labels

        options = composer._options_keyboard(draft)
        option_labels = [button.text for row in options.inline_keyboard for button in row]
        for key in ("posting_window", "auto_delete", "self_destruct", "auto_pin"):
            assert translate_post_composer(locale, key) in " ".join(option_labels)

        buttons = composer._buttons_keyboard(0, locale)
        button_labels = [button.text for row in buttons.inline_keyboard for button in row]
        assert translate_post_composer(locale, "add_buttons") in button_labels
        assert translate_post_composer(locale, "done") in button_labels

        weekdays = composer._days_keyboard(set(), locale)
        weekday_labels = composer._weekdays({"language": locale})
        displayed = " ".join(button.text for row in weekdays.inline_keyboard for button in row)
        assert all(day in displayed for day in weekday_labels)


def test_my_posts_and_owner_panel_keyboards_follow_selected_locale():
    posts = [{"_id": "post-1", "status": "pending"}]
    for locale in LANGUAGES:
        posts_keyboard = base._posts_keyboard(posts, 0, locale, "all")
        post_labels = [button.text for row in posts_keyboard.inline_keyboard for button in row]
        assert translate_my_posts(locale, "all") in " ".join(post_labels)
        assert translate_my_posts(locale, "active") in " ".join(post_labels)
        assert translate(locale, "pause") in " ".join(post_labels)
        assert translate(locale, "delete") in " ".join(post_labels)
        assert translate(locale, "clone") in " ".join(post_labels)

        owner_keyboard = owner_panel._owner_menu_keyboard(locale)
        owner_labels = [button.text for row in owner_keyboard.inline_keyboard for button in row]
        for key in ("status", "users", "groups", "broadcast_all", "broadcast_one", "clear_data"):
            assert translate_owner_panel(locale, key) in " ".join(owner_labels)


def test_permission_and_reply_keyboard_feedback_are_localized():
    keys = ("unavailable", "admin", "channel_admin", "post_messages", "not_member", "restricted")
    for locale in LANGUAGES:
        for key in keys:
            message = permission_message(locale, key)
            assert message and message.strip()
        for key in ("open_link", "unsupported_action", "menu_expired", "action_failed"):
            message = translate_system(locale, key)
            assert message and message.strip()


def test_auto_delete_and_auto_pin_duration_units_support_every_selected_language():
    samples = {
        "en": ("5 minutes", "2 hours"),
        "my": ("5 မိနစ်", "2 နာရီ"),
        "zh": ("5 分钟", "2 小时"),
        "es": ("5 minutos", "2 horas"),
        "hi": ("5 मिनट", "2 घंटे"),
        "ar": ("5 دقائق", "2 ساعات"),
        "pt": ("5 minutos", "2 horas"),
        "fr": ("5 minutes", "2 heures"),
        "ru": ("5 минут", "2 часа"),
        "bn": ("5 মিনিট", "2 ঘণ্টা"),
        "id": ("5 menit", "2 jam"),
        "ja": ("5 分", "2 時間"),
        "ko": ("5 분", "2 시간"),
        "th": ("5 นาที", "2 ชั่วโมง"),
        "vi": ("5 phút", "2 giờ"),
        "tr": ("5 dakika", "2 saat"),
        "de": ("5 Minuten", "2 Stunden"),
        "it": ("5 minuti", "2 ore"),
        "ur": ("5 منٹ", "2 گھنٹے"),
        "sw": ("5 dakika", "2 saa"),
    }
    for locale, (minutes, hours) in samples.items():
        assert composer._duration_to_hours(minutes, max_minutes=525600, language=locale) == 5 / 60
        assert composer._duration_to_hours(hours, max_minutes=87600 * 60, language=locale) == 2


def test_schedule_flow_translations_remain_complete_and_format_cleanly():
    assert set(FLOW_TRANSLATIONS) == set(LANGUAGES)
    assert len(FLOW_KEYS) == len(set(FLOW_KEYS))
    for locale in LANGUAGES:
        for key in FLOW_KEYS:
            template = FLOW_TRANSLATIONS[locale][key]
            values = {name: "TEST" for name in re.findall(r"\{([a-z_]+)\}", template)}
            rendered = translate_schedule_flow(locale, key, **values)
            assert rendered and "{" not in rendered and "}" not in rendered
