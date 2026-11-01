"""Regression tests for the private-chat Reply Keyboard bridge."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
)

from reply_keyboard import (
    handle_reply_keyboard_message,
    publish_reply_keyboard,
    register_reply_keyboard_routes,
    split_inline_keyboard,
)


def _labels(markup):
    return [button.text for row in markup.keyboard for button in row]


def test_only_add_to_group_and_share_bot_remain_inline():
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add to My Group",
                    url="https://t.me/Scheduler?startgroup=true",
                ),
                InlineKeyboardButton(
                    "📤 Share Bot",
                    url="https://t.me/share/url?url=https://t.me/Scheduler",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📅 Schedule New Post", callback_data="action:schedule_new"
                ),
                InlineKeyboardButton("📋 My Posts", callback_data="action:my_posts"),
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data="nav:help"),
                InlineKeyboardButton("⚙️ Language", callback_data="nav:language"),
            ],
        ]
    )

    inline, reply, routes = split_inline_keyboard(markup)

    assert [
        button.text for row in inline.inline_keyboard for button in row
    ] == ["➕ Add to My Group", "📤 Share Bot"]
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert set(_labels(reply)) == {
        "📅 Schedule New Post",
        "📋 My Posts",
        "❓ Help",
        "⚙️ Language",
    }
    assert routes["📅 Schedule New Post"] == {
        "type": "callback",
        "data": "action:schedule_new",
    }
    assert routes["📋 My Posts"] == {
        "type": "callback",
        "data": "action:my_posts",
    }


def test_non_welcome_url_button_becomes_a_reply_choice():
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📩 Open Bot in Private Chat",
                    url="https://t.me/Scheduler?start=setup_-100123",
                )
            ]
        ]
    )
    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert _labels(reply) == ["📩 Open Bot in Private Chat"]
    assert routes["📩 Open Bot in Private Chat"] == {
        "type": "url",
        "url": "https://t.me/Scheduler?start=setup_-100123",
    }


def test_conflicting_duplicate_button_labels_are_disambiguated():
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Next", callback_data="page:1")],
            [InlineKeyboardButton("Next", callback_data="page:2")],
        ]
    )
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


def test_publish_reply_keyboard_uses_a_non_visible_anchor_without_arrow():
    async def scenario():
        chat_id = 123456
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
            delete_message=AsyncMock(),
        )
        bot_data = {}
        target_message = SimpleNamespace(chat_id=chat_id, message_id=600)
        markup = ReplyKeyboardMarkup([["📋 My Posts"]])
        routes = {
            "📋 My Posts": {"type": "callback", "data": "action:my_posts"}
        }

        anchor = await publish_reply_keyboard(
            bot, bot_data, chat_id, markup, routes, target_message
        )

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["text"] == "\u2063"
        assert "⬇️" not in kwargs["text"]
        assert kwargs["reply_markup"] is markup
        assert anchor.message_id == 900
        entry = bot_data["_reply_keyboard_route_registry"][str(chat_id)]
        assert entry["routes"] == routes
        assert entry["target_message"] is target_message
        assert entry["anchor_message_id"] == 900

    asyncio.run(scenario())


def test_private_reply_keyboard_choice_is_queued_and_dispatched_normally():
    async def scenario():
        chat_id = 123456
        token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        application = ApplicationBuilder().token(token).build()
        handled = []

        async def callback_handler(update, context):
            query = update.callback_query
            handled.append(
                (
                    query.data,
                    query.from_user.id,
                    query.message.message_id,
                )
            )

        application.add_handler(
            CallbackQueryHandler(
                callback_handler, pattern=r"^action:schedule_new$"
            )
        )

        bot_user = {
            "id": 123456,
            "is_bot": True,
            "first_name": "Scheduler",
        }
        user_data = {
            "id": chat_id,
            "is_bot": False,
            "first_name": "Test",
        }
        chat_data = {
            "id": chat_id,
            "type": "private",
            "first_name": "Test",
        }
        target_message = Message.de_json(
            {
                "message_id": 600,
                "date": 1700000000,
                "chat": chat_data,
                "from": bot_user,
                "text": "Main menu",
            },
            application.bot,
        )
        tapped_message = Message.de_json(
            {
                "message_id": 700,
                "date": 1700000001,
                "chat": chat_data,
                "from": user_data,
                "text": "📅 Schedule New Post",
            },
            application.bot,
        )

        bot_data = application.bot_data
        register_reply_keyboard_routes(
            bot_data,
            chat_id,
            {
                "📅 Schedule New Post": {
                    "type": "callback",
                    "data": "action:schedule_new",
                }
            },
            target_message,
        )
        update = Update(update_id=701, message=tapped_message)
        context = SimpleNamespace(
            bot_data=bot_data,
            application=application,
        )

        with pytest.raises(ApplicationHandlerStop):
            await handle_reply_keyboard_message(update, context)

        assert handled == []
        assert application.update_queue.qsize() == 1
        synthetic_update = application.update_queue.get_nowait()
        assert synthetic_update.callback_query.data == "action:schedule_new"
        assert synthetic_update.callback_query.from_user.id == chat_id
        assert synthetic_update.callback_query.message.message_id == 600

        # Process after the original message handler has returned. This
        # exercises PTB's normal sequential dispatcher rather than recursively
        # entering Application.process_update from inside an active update.
        await application.process_update(synthetic_update)
        assert handled == [("action:schedule_new", chat_id, 600)]

    asyncio.run(scenario())


def test_unmatched_text_is_left_for_the_active_conversation():
    async def scenario():
        chat_id = 123456
        message = SimpleNamespace(
            text="This is user-entered post content",
            message_id=702,
            reply_text=AsyncMock(),
        )
        queue = asyncio.Queue()
        bot_data = {}
        context = SimpleNamespace(
            bot_data=bot_data,
            application=SimpleNamespace(
                bot_data=bot_data,
                update_queue=queue,
            ),
        )
        update = SimpleNamespace(
            update_id=703,
            message=message,
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(id=chat_id),
        )

        result = await handle_reply_keyboard_message(update, context)

        assert result is None
        assert queue.empty()
        assert "chat_cleanup" not in bot_data
        message.reply_text.assert_not_awaited()

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
