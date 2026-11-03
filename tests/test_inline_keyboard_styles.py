"""Regression tests for semantic inline keyboard button styles."""

from handlers.base import confirm_keyboard
from handlers.schedule_wizard import _first_run_keyboard, _recurrence_keyboard


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_shared_confirmation_keyboard_uses_success_and_danger():
    buttons = _buttons(confirm_keyboard("confirm:save", "nav:back"))

    assert buttons[0].style == "success"
    assert buttons[1].style is None
    assert buttons[2].style == "danger"


def test_schedule_new_post_keyboards_keep_cancel_actions_danger_styled():
    for markup in (_first_run_keyboard("schedule:back"), _recurrence_keyboard("en")):
        buttons = _buttons(markup)
        assert buttons[-1].style == "danger"
