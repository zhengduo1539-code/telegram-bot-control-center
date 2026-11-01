"""Regression coverage proving the Templates feature is no longer exposed."""
from handlers.base import _posts_keyboard, main_menu_keyboard
from handlers.queue_manager import (
    _mp_add_item_keyboard,
    _qm_content_keyboard,
    build_media_pool,
    build_queue_manager,
)
from handlers.schedule_wizard import _content_step_keyboard, build_schedule_wizard
from localization import LANGUAGES, translate
from welcome_localization import welcome_text


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_templates_are_absent_from_every_localized_welcome_help_and_menu():
    for language in LANGUAGES:
        assert "📝" not in welcome_text(language)
        assert "📝" not in translate(language, "help_text")
        menu_buttons = _buttons(main_menu_keyboard(language=language))
        assert all(button.callback_data != "action:templates" for button in menu_buttons)


def test_templates_are_absent_from_all_content_entry_keyboards():
    assert all(
        "template" not in (button.callback_data or "").lower()
        for keyboard in (
            _content_step_keyboard(),
            _qm_content_keyboard("en"),
            _mp_add_item_keyboard("en"),
        )
        for button in _buttons(keyboard)
    )


def test_my_posts_no_longer_offers_save_as_template():
    posts = [{
        "_id": "0123456789abcdef01234567",
        "status": "pending",
        "chat_id": -1001234567890,
        "recurrence": {"type": "once"},
        "timezone": "UTC",
    }]
    callbacks = [button.callback_data for button in _buttons(_posts_keyboard(posts, 0))]
    assert not any("template" in (callback or "").lower() for callback in callbacks)


def test_template_picker_conversation_states_are_not_registered():
    assert 28 not in build_schedule_wizard().states
    assert 112 not in build_queue_manager().states
    assert 212 not in build_media_pool().states
