"""Regression tests for semantic inline keyboard button styles."""

from handlers.base import confirm_keyboard
from handlers.queue_manager import _mp_schedule_confirm_keyboard, _qm_confirm_keyboard
from handlers.templates import _tmpl_list_keyboard


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_shared_confirmation_keyboard_uses_success_and_danger():
    buttons = _buttons(confirm_keyboard("confirm:save", "nav:back"))

    assert buttons[0].style == "success"
    assert buttons[1].style is None
    assert buttons[2].style == "danger"


def test_queue_confirmation_keyboards_highlight_only_commit_and_cancel():
    for markup in (
        _qm_confirm_keyboard("en", "queue:confirm", "queue:back"),
        _mp_schedule_confirm_keyboard("en"),
    ):
        buttons = _buttons(markup)
        assert buttons[0].style == "success"
        assert buttons[1].style is None
        assert buttons[2].style == "danger"


def test_template_list_marks_delete_as_destructive_only():
    markup = _tmpl_list_keyboard([{"_id": "0123456789abcdef01234567", "name": "Example"}], 0)
    buttons = _buttons(markup)

    assert buttons[0].style is None
    assert buttons[1].style == "danger"
    assert buttons[-1].style is None
