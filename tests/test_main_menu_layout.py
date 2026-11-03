"""Regression tests for the Reply Keyboard-only Main Menu."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers.base import main_menu_keyboard, replace_with_main_menu_message
from reply_keyboard import split_inline_keyboard


def test_main_menu_exposes_only_reply_keyboard_routes():
    bot = SimpleNamespace(username="ScheduleBot")
    markup = main_menu_keyboard(bot=bot, user_id=123456789, language="en")

    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert reply is not None
    labels = {button.text for row in reply.keyboard for button in row}
    assert {
        "➕ Add to My Group",
        "📤 Share Bot",
        "📅 Schedule New Post",
        "📋 My Posts",
        "🌐 Language",
    }.issubset(labels)
    assert routes["📅 Schedule New Post"]["data"] == "action:schedule_new"
    assert routes["📋 My Posts"]["data"] == "action:my_posts"
    assert routes["➕ Add to My Group"]["type"] == "url"
    assert routes["📤 Share Bot"]["type"] == "url"
    assert not any("My Stats" in label or "Feature Guide" in label for label in labels)


def test_menu_navigation_replaces_the_old_screen_with_a_keyboard_ready_message():
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
