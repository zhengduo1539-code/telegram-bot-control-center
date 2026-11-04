"""Regression tests for the mixed Inline / Reply Keyboard Main Menu."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers.base import main_menu_keyboard, replace_with_main_menu_message
from reply_keyboard import split_inline_keyboard


def test_main_menu_keeps_welcome_actions_inline_and_features_on_reply_keyboard(monkeypatch):
    monkeypatch.setattr("handlers.base.ADMIN_IDS", {123456789})
    bot = SimpleNamespace(username="ScheduleBot")
    markup = main_menu_keyboard(bot=bot, user_id=123456789, language="en")

    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is not None
    assert [
        button.text for row in inline.inline_keyboard for button in row
    ] == ["➕ Add to My Group", "📤 Share Bot"]
    assert reply is not None
    reply_buttons = [button for row in reply.keyboard for button in row]
    labels = {button.text for row in reply.keyboard for button in row}
    assert len(reply_buttons) == 4  # Schedule, My Posts, Language, and Owner Panel
    assert all(button.style == "primary" for button in reply_buttons)
    assert {
        "📅 Schedule New Post",
        "📋 My Posts",
        "🌐 Language",
    }.issubset(labels)
    assert "➕ Add to My Group" not in labels
    assert "📤 Share Bot" not in labels
    assert routes["📅 Schedule New Post"]["data"] == "action:schedule_new"
    assert routes["📋 My Posts"]["data"] == "action:my_posts"
    assert "➕ Add to My Group" not in routes
    assert "📤 Share Bot" not in routes
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
        assert inline is not None
        assert [
            button.text for row in inline.inline_keyboard for button in row
        ] == ["➕ Add to My Group", "📤 Share Bot"]
        assert reply is not None
        labels = {button.text for row in reply.keyboard for button in row}
        assert "➕ Add to My Group" not in labels
        assert "📤 Share Bot" not in labels
        assert routes["📅 Schedule New Post"]["data"] == "action:schedule_new"
        bot.delete_message.assert_awaited_once_with(
            chat_id=old_message.chat_id,
            message_id=old_message.message_id,
        )

    asyncio.run(scenario())
