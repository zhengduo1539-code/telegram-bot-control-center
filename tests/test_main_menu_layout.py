"""Regression tests for the private Main Menu Inline + Reply Keyboard split."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers.base import main_menu_keyboard, nav_keyboard, replace_with_main_menu_message
from reply_keyboard import split_inline_keyboard


def _inline_labels(markup):
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]


def _inline_urls(markup):
    return [
        button.url
        for row in markup.inline_keyboard
        for button in row
        if button.url
    ]


def test_main_menu_keeps_add_share_inline_and_feature_actions_in_reply_keyboard():
    bot = SimpleNamespace(username="ScheduleBot")
    markup = main_menu_keyboard(bot=bot, user_id=123456789, language="en")

    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is not None
    assert _inline_labels(inline) == ["➕ Add to My Group", "📤 Share Bot"]
    urls = _inline_urls(inline)
    assert urls[0] == "https://t.me/ScheduleBot?startgroup=true"
    assert urls[1].startswith("https://t.me/share/url?")
    assert "url=https%3A%2F%2Ft.me%2FScheduleBot" in urls[1]

    assert reply is not None
    callback_data = {route["data"] for route in routes.values()}
    assert {
        "action:schedule_new",
        "action:my_posts",
        "nav:language",
    }.issubset(callback_data)
    assert "action:my_stats" not in callback_data
    assert "nav:help" not in callback_data
    assert "action:manage_queue" not in callback_data
    assert "action:media_pool" not in callback_data
    assert "nav:cancel" not in callback_data
    assert any(
        button.text == "❌ Cancel"
        for row in nav_keyboard().inline_keyboard
        for button in row
    )
    assert all(route["type"] == "callback" for route in routes.values())
    assert not any("Add to My Group" in label or "Share Bot" in label for label in routes)


def test_menu_navigation_sends_inline_actions_with_reply_keyboard_before_deleting_old_screen():
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
            username="ScheduleBot",
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
        assert inline is not None
        assert _inline_labels(inline) == ["➕ Add to My Group", "📤 Share Bot"]
        assert reply is not None
        assert routes["📅 Schedule New Post"]["data"] == "action:schedule_new"
        bot.delete_message.assert_awaited_once_with(
            chat_id=old_message.chat_id,
            message_id=old_message.message_id,
        )

    asyncio.run(scenario())
