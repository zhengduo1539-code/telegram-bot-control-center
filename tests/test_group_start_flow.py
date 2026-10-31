"""Regression tests for group /start → private setup → preselected scheduling."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from handlers.base import (
    MAIN_MENU,
    build_group_start_payload,
    cmd_start,
    parse_group_start_payload,
)
from handlers.schedule_wizard import WIZARD_CONTENT, WIZARD_INTRO, cb_wizard_continue, enter_wizard


def test_group_start_payload_round_trips_negative_chat_id():
    chat_id = -1001234567890
    payload = build_group_start_payload(chat_id)

    assert payload == "setup_-1001234567890"
    assert len(payload.encode("utf-8")) <= 64
    assert parse_group_start_payload([payload]) == chat_id


def test_group_start_payload_rejects_invalid_or_non_group_values():
    assert parse_group_start_payload([]) is None
    assert parse_group_start_payload(["other_payload"]) is None
    assert parse_group_start_payload(["setup_123456"]) is None
    assert parse_group_start_payload(["setup_-"]) is None
    assert parse_group_start_payload(["setup_-12x34"]) is None
    assert parse_group_start_payload(["setup_" + "-" + ("9" * 22)]) is None


def test_group_start_shows_private_chat_button_not_feature_menu():
    async def scenario():
        chat_id = -1001234567890
        message = SimpleNamespace(reply_text=AsyncMock())
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="ScheduleBot"))
        )
        context = SimpleNamespace(bot=bot, args=[], user_data={})
        update = SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(
                type="supergroup", id=chat_id, title="Publisher Group"
            ),
            effective_user=SimpleNamespace(id=123456789),
        )

        state = await cmd_start(update, context)

        assert state == ConversationHandler.END
        bot.get_me.assert_awaited_once()
        kwargs = message.reply_text.await_args.kwargs
        keyboard = kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].text == "📩 Open Bot in Private Chat"
        assert keyboard.inline_keyboard[0][0].url == (
            "https://t.me/ScheduleBot?start=setup_-1001234567890"
        )
        assert "Publisher Group" in message.reply_text.await_args.args[0]
        assert "private chat" in message.reply_text.await_args.args[0].lower()
        assert "action:schedule_new" not in str(keyboard.inline_keyboard)

    asyncio.run(scenario())


def test_private_start_validates_and_remembers_deep_linked_group():
    async def scenario():
        chat_id = -1001234567890
        target_chat = SimpleNamespace(id=chat_id, title="Publisher Group", type="supergroup")
        message = SimpleNamespace(reply_text=AsyncMock())
        bot = SimpleNamespace(username="ScheduleBot")
        context = SimpleNamespace(
            bot=bot,
            args=[build_group_start_payload(chat_id)],
            user_data={},
        )
        update = SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(type="private", id=123456789, title=None),
            effective_user=SimpleNamespace(id=123456789),
        )

        with patch("handlers.base.ensure_chat_access", new=AsyncMock(return_value=target_chat)) as access:
            state = await cmd_start(update, context)

        assert state == MAIN_MENU
        access.assert_awaited_once_with(bot, chat_id, 123456789)
        assert context.user_data["launch_chat"] == {
            "chat_id": chat_id,
            "title": "Publisher Group",
        }
        text = message.reply_text.await_args.args[0]
        assert "Private Setup" in text
        assert "Publisher Group" in text
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        callback_data = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        assert "action:schedule_new" in callback_data
        assert "action:manage_queue" in callback_data

    asyncio.run(scenario())


def test_schedule_wizard_preselects_linked_group_and_skips_picker():
    async def scenario():
        chat_id = -1001234567890
        target_chat = SimpleNamespace(id=chat_id, title="Publisher Group", type="supergroup")
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="ScheduleBot"))
        )
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=123456789, message_id=55),
            from_user=SimpleNamespace(id=123456789),
            get_bot=lambda: bot,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={"launch_chat": {"chat_id": chat_id, "title": "Publisher Group"}},
            application=SimpleNamespace(bot_data={}),
            bot=bot,
        )

        with patch("handlers.schedule_wizard.resolve_user_language", new=AsyncMock(return_value="en")), patch(
            "handlers.schedule_wizard.ensure_chat_access",
            new=AsyncMock(return_value=target_chat),
        ) as access, patch(
            "handlers.schedule_wizard.list_groups_for_user",
            new=AsyncMock(side_effect=AssertionError("group picker should be bypassed")),
        ):
            state = await enter_wizard(update, context)
            assert state == WIZARD_INTRO
            assert "launch_chat" in context.user_data
            state = await cb_wizard_continue(update, context)

        assert state == WIZARD_CONTENT
        access.assert_awaited_once_with(context.bot, chat_id, 123456789)
        assert "launch_chat" not in context.user_data
        assert context.user_data["wizard"]["chat_id"] == chat_id
        assert context.user_data["wizard"]["chat_title"] == "Publisher Group"
        assert "Step 2/8 — Post Content" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())
