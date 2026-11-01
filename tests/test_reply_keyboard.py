"""Regression tests for the all-private-actions Reply Keyboard bridge."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, User
from telegram.ext import ApplicationHandlerStop

from reply_keyboard import (
    handle_reply_keyboard_message,
    register_reply_keyboard_routes,
    split_inline_keyboard,
)


def _labels(markup):
    return [button.text for row in markup.keyboard for button in row]


def test_only_add_to_group_and_share_bot_remain_inline():
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add to My Group", url="https://t.me/Scheduler?startgroup=true"),
            InlineKeyboardButton("📤 Share Bot", url="https://t.me/share/url?url=https://t.me/Scheduler"),
        ],
        [
            InlineKeyboardButton("📅 Schedule New Post", callback_data="action:schedule_new"),
            InlineKeyboardButton("📋 My Posts", callback_data="action:my_posts"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="nav:help"),
            InlineKeyboardButton("⚙️ Language", callback_data="nav:language"),
        ],
    ])

    inline, reply, routes = split_inline_keyboard(markup)

    assert [button.text for row in inline.inline_keyboard for button in row] == [
        "➕ Add to My Group", "📤 Share Bot"
    ]
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert set(_labels(reply)) == {
        "📅 Schedule New Post", "📋 My Posts", "❓ Help", "⚙️ Language"
    }
    assert routes["📅 Schedule New Post"] == {
        "type": "callback", "data": "action:schedule_new"
    }
    assert routes["📋 My Posts"] == {"type": "callback", "data": "action:my_posts"}


def test_non_welcome_url_button_becomes_a_reply_choice():
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📩 Open Bot in Private Chat",
            url="https://t.me/Scheduler?start=setup_-100123",
        )
    ]])
    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert _labels(reply) == ["📩 Open Bot in Private Chat"]
    assert routes["📩 Open Bot in Private Chat"] == {
        "type": "url", "url": "https://t.me/Scheduler?start=setup_-100123"
    }


def test_conflicting_duplicate_button_labels_are_disambiguated():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Next", callback_data="page:1")],
        [InlineKeyboardButton("Next", callback_data="page:2")],
    ])
    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert _labels(reply) == ["Next", "Next (2)"]
    assert routes["Next"]["data"] == "page:1"
    assert routes["Next (2)"]["data"] == "page:2"


def test_empty_inline_keyboard_returns_no_markup():
    inline, reply, routes = split_inline_keyboard(InlineKeyboardMarkup([]))
    assert inline is None
    assert reply is None
    assert routes == {}



def test_private_reply_keyboard_choice_dispatches_original_callback_route():
    async def scenario():
        chat_id = 123456
        user = User(id=chat_id, first_name="Test", is_bot=False)
        chat = SimpleNamespace(id=chat_id)
        tapped_message = SimpleNamespace(
            text="📅 Schedule New Post",
            message_id=700,
            reply_text=AsyncMock(),
        )
        target_message = SimpleNamespace(
            chat_id=chat_id,
            message_id=600,
            chat=chat,
        )
        bot_data = {}
        register_reply_keyboard_routes(
            bot_data,
            chat_id,
            {"📅 Schedule New Post": {"type": "callback", "data": "action:schedule_new"}},
            target_message,
        )
        application = SimpleNamespace(
            bot_data=bot_data,
            process_update=AsyncMock(),
        )
        context = SimpleNamespace(bot_data=bot_data, application=application)
        update = SimpleNamespace(
            update_id=701,
            message=tapped_message,
            effective_chat=chat,
            effective_user=user,
        )

        with pytest.raises(ApplicationHandlerStop):
            await handle_reply_keyboard_message(update, context)

        application.process_update.assert_awaited_once()
        synthetic = application.process_update.await_args.args[0]
        assert synthetic.callback_query.data == "action:schedule_new"
        assert synthetic.callback_query.from_user.id == chat_id
        assert synthetic.callback_query.message.message_id == 600
        assert bot_data["chat_cleanup"][chat_id] == [700]

    asyncio.run(scenario())


def test_group_start_reply_keyboard_url_choice_returns_deep_link():
    async def scenario():
        chat_id = -1001234567890
        chat = SimpleNamespace(id=chat_id)
        message = SimpleNamespace(
            text="📩 Open Bot in Private Chat",
            message_id=702,
            reply_text=AsyncMock(),
        )
        bot_data = {}
        register_reply_keyboard_routes(
            bot_data,
            chat_id,
            {
                "📩 Open Bot in Private Chat": {
                    "type": "url",
                    "url": "https://t.me/Scheduler?start=setup_-1001234567890",
                }
            },
            SimpleNamespace(chat_id=chat_id, message_id=600),
        )
        context = SimpleNamespace(
            bot_data=bot_data,
            application=SimpleNamespace(bot_data=bot_data),
        )
        update = SimpleNamespace(
            update_id=703,
            message=message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=321),
        )

        with pytest.raises(ApplicationHandlerStop):
            await handle_reply_keyboard_message(update, context)

        message.reply_text.assert_awaited_once_with(
            "🔗 Open this link:\nhttps://t.me/Scheduler?start=setup_-1001234567890"
        )
        assert bot_data["chat_cleanup"][chat_id] == [702]

    asyncio.run(scenario())
