"""Regression tests for the single-message private Main Menu layout."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers.base import (
    main_menu_keyboard,
    main_menu_links,
    replace_with_main_menu_message,
)
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


def test_menu_navigation_sends_direct_menu_before_deleting_previous_screen():
    async def scenario():
        events = []
        old_message = SimpleNamespace(chat_id=123456, message_id=90)
        query = SimpleNamespace(message=old_message)
        new_message = SimpleNamespace(message_id=91)

        async def send_message(**kwargs):
            events.append("send")
            return new_message

        async def delete_message(**kwargs):
            events.append("delete")
            return True

        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=send_message),
            delete_message=AsyncMock(side_effect=delete_message),
        )
        context = SimpleNamespace(bot=bot)
        text = "🏠 Main Menu"

        result = await replace_with_main_menu_message(
            query, context, text, 123456789, "en"
        )

        assert result is new_message
        assert events == ["send", "delete"]
        bot.send_message.assert_awaited_once()
        sent = bot.send_message.await_args.kwargs
        assert sent["chat_id"] == old_message.chat_id
        assert sent["text"] == text
        inline, reply, routes = split_inline_keyboard(sent["reply_markup"])
        assert inline is None
        assert reply is not None
        assert routes["📅 Schedule New Post"]["data"] == "action:schedule_new"
        bot.delete_message.assert_awaited_once_with(
            chat_id=old_message.chat_id,
            message_id=old_message.message_id,
        )

    asyncio.run(scenario())
