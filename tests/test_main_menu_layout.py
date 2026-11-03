"""Regression tests for the single-message private Main Menu layout."""

from types import SimpleNamespace

from handlers.base import main_menu_keyboard, main_menu_links
from reply_keyboard import split_inline_keyboard


def test_main_menu_actions_are_all_reply_keyboard_routes():
    bot = SimpleNamespace(username="ScheduleBot")
    markup = main_menu_keyboard(bot=bot, user_id=123456789, language="en")

    inline_markup, reply_markup, routes = split_inline_keyboard(markup)

    assert inline_markup is None
    assert reply_markup is not None
    callback_data = {route["data"] for route in routes.values()}
    assert {
        "action:schedule_new",
        "action:my_posts",
        "action:manage_queue",
        "action:media_pool",
        "action:my_stats",
        "nav:language",
        "nav:help",
        "nav:cancel",
        "owner:panel",
    }.issubset(callback_data)
    assert all(route["type"] == "callback" for route in routes.values())
    assert not any(
        "Add to My Group" in label or "Share Bot" in label
        for label in routes
    )


def test_add_group_and_share_are_rendered_as_clickable_message_links():
    bot = SimpleNamespace(username="ScheduleBot")
    links = main_menu_links(bot, "en")

    assert "[➕ Add to My Group](https://t.me/ScheduleBot?startgroup=true)" in links
    assert "[📤 Share Bot](https://t.me/share/url?" in links
    assert "url=https%3A%2F%2Ft.me%2FScheduleBot" in links
    assert "Check+out+this+Advanced+Scheduler+Bot" in links
