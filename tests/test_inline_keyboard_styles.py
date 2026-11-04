"""Regression tests for semantic inline keyboard button styles."""

from handlers.base import confirm_keyboard, nav_keyboard
from handlers.owner_panel import _cancel_only_keyboard
from handlers.schedule_wizard import (
    _advanced_keyboard,
    _dow_keyboard,
    _first_run_keyboard,
    _inline_btns_keyboard,
    _lifecycle_keyboard,
    _recurrence_keyboard,
    _time_window_keyboard,
    _wizard_intro_keyboard,
)
from reply_keyboard import split_inline_keyboard


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



def _button(markup, label):
    return next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text.casefold() == label.casefold()
    )


def test_wizard_continue_and_next_actions_use_success_style():
    intro = _wizard_intro_keyboard("en")
    assert _button(intro, "Continue ➡️").style == "success"

    for markup in (
        _dow_keyboard(set()),
        _time_window_keyboard(False),
        _lifecycle_keyboard(False, False, False),
        _advanced_keyboard(False, False, False, False),
    ):
        assert _button(markup, "➡️ Next").style == "success"


def test_cancel_actions_use_danger_style_across_wizard_and_owner_flows():
    for markup in (
        nav_keyboard("nav:back"),
        _wizard_intro_keyboard("en"),
        _first_run_keyboard("wizard:back"),
        _recurrence_keyboard("en"),
        _inline_btns_keyboard(0),
        _cancel_only_keyboard(),
    ):
        cancel_buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
            if "cancel" in button.text.casefold()
        ]
        assert cancel_buttons
        assert all(button.style == "danger" for button in cancel_buttons)


def test_done_and_confirmation_actions_use_success_style():
    assert _button(_inline_btns_keyboard(1), "✅ Done").style == "success"

    confirm_buttons = _buttons(confirm_keyboard("confirm:save", "nav:back"))
    assert confirm_buttons[0].style == "success"
    assert confirm_buttons[-1].style == "danger"


def test_reply_keyboard_bridge_preserves_success_and_danger_styles():
    inline = _wizard_intro_keyboard("en")
    _, reply, _ = split_inline_keyboard(inline)

    assert reply is not None
    buttons = [button for row in reply.keyboard for button in row]
    assert [button.style for button in buttons] == ["danger", "success"]
