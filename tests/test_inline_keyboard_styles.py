"""Regression tests for semantic inline keyboard button styles."""

from handlers.base import confirm_keyboard, nav_keyboard, _posts_keyboard, start_keyboard
from handlers.owner_panel import _cancel_only_keyboard, _owner_menu_keyboard
from handlers.schedule_wizard import (
    _advanced_keyboard,
    _chat_list_keyboard,
    _content_step_keyboard,
    _dow_keyboard,
    _first_run_keyboard,
    _inline_btns_keyboard,
    _interval_unit_keyboard,
    _lifecycle_keyboard,
    _max_runs_keyboard,
    _no_groups_keyboard,
    _recurrence_keyboard,
    _timezone_keyboard,
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
        _chat_list_keyboard([], 0, "ScheduleBot", "en"),
        _no_groups_keyboard("ScheduleBot", "en"),
        _wizard_intro_keyboard("en"),
        _first_run_keyboard("wizard:back"),
        _recurrence_keyboard("en"),
        _interval_unit_keyboard(),
        _dow_keyboard(set()),
        _timezone_keyboard(0, "en"),
        _time_window_keyboard(False),
        _lifecycle_keyboard(False, False, False),
        _advanced_keyboard(False, False, False, False),
        _content_step_keyboard(),
        _inline_btns_keyboard(0),
        _max_runs_keyboard(),
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

def test_group_selection_and_manual_group_id_buttons_have_requested_styles():
    group_picker = _chat_list_keyboard(
        [{"chat_id": -1001234567890, "title": "Example Group", "chat_type": "supergroup"}],
        0,
        "ScheduleBot",
        "en",
    )
    assert _button(group_picker, "👥 Example Group").style == "primary"
    assert _button(group_picker, "✏️ Enter Chat ID manually").style == "success"
    assert _button(group_picker, "❌ Cancel").style == "danger"

    no_groups = _no_groups_keyboard("ScheduleBot", "en")
    assert _button(no_groups, "➕ Add Bot to a Group").style == "primary"
    assert _button(no_groups, "✏️ Enter Chat ID manually").style == "success"
    assert _button(no_groups, "❌ Cancel").style == "danger"


def test_schedule_choice_buttons_use_primary_and_selected_options_use_success():
    assert all(
        button.style == "primary"
        for button in _buttons(_recurrence_keyboard("en"))[:3]
    )
    assert all(
        button.style == "primary"
        for button in _buttons(_interval_unit_keyboard())[:3]
    )

    days = _buttons(_dow_keyboard({0, 2}))
    assert [button.style for button in days[:7]] == [
        "success", "primary", "success", "primary", "primary", "primary", "primary"
    ]

    time_window_on = _buttons(_time_window_keyboard(True))
    time_window_off = _buttons(_time_window_keyboard(False))
    assert [button.style for button in time_window_on[:2]] == ["success", "primary"]
    assert [button.style for button in time_window_off[:2]] == ["primary", "success"]

    lifecycle = _buttons(_lifecycle_keyboard(True, False, True))
    assert [button.style for button in lifecycle[:3]] == ["success", "primary", "success"]
    advanced = _buttons(_advanced_keyboard(True, False, True, False))
    assert [button.style for button in advanced[:4]] == [
        "success", "primary", "success", "primary"
    ]

    timezone = _buttons(_timezone_keyboard(0, "en"))
    assert all(button.style == "primary" for button in timezone[:-3])
    assert timezone[-3].style == "primary"  # Next page
    assert timezone[-2].style is None       # Back
    assert timezone[-1].style == "danger"    # Cancel


def test_owner_panel_and_my_posts_actions_are_semantically_styled():
    owner_buttons = _buttons(_owner_menu_keyboard())
    assert all(button.style == "primary" for button in owner_buttons[:4])
    assert owner_buttons[4].style == "danger"
    assert owner_buttons[5].style == "primary"

    posts = [
        {"_id": "post-1", "status": "pending"},
        {"_id": "post-2", "status": "paused"},
        {"_id": "post-3", "status": "posted"},
        {"_id": "post-4", "status": "failed"},
        {"_id": "post-5", "status": "pending"},
        {"_id": "post-6", "status": "pending"},
    ]
    keyboard = _posts_keyboard(posts, 0, "en", "all")
    buttons = _buttons(keyboard)
    assert buttons[0].style == "success"  # selected "All" filter
    assert buttons[1].style == "primary"  # unselected status filters
    assert buttons[5].style == "primary"  # Pause
    assert buttons[6].style == "danger"   # Delete
    assert buttons[7].style == "primary"  # Clone entry action
    assert buttons[8].style == "success"  # Resume
    assert buttons[-1].style == "primary" # Main Menu


def test_welcome_and_completed_schedule_actions_use_primary_style():
    welcome = _buttons(start_keyboard(type("Bot", (), {"username": "ScheduleBot"})(), "en"))
    assert [button.style for button in welcome] == ["primary", "primary"]

