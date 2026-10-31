"""Regression tests for the Media Pool onboarding and scoped management flow."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardMarkup

from database import COL_MEDIA_POOLS, COL_POSTS
from handlers import queue_manager as media_pool


def _button_labels(keyboard):
    return [
        button.text
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_media_pool_menu_has_guided_actions_not_global_destructive_buttons():
    labels = _button_labels(media_pool._mp_menu_keyboard())

    assert "➕ Add Content" in labels
    assert "⏱ Set Posting Schedule" in labels
    assert "📊 Pool Overview" in labels
    assert "🛠 Manage Pool" in labels
    assert "🔄 Reset Pool" not in labels
    assert "🗑 Clear All" not in labels


def test_media_pool_group_picker_shows_known_groups_and_manual_fallback():
    keyboard = media_pool._mp_group_picker_keyboard(
        [{
            "chat_id": -1001234567890,
            "title": "Publisher Group",
            "chat_type": "supergroup",
        }],
        page=0,
    )
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "mp:pick:-1001234567890" in callbacks
    assert media_pool.CB_MP_MANUAL in callbacks
    assert media_pool.CB_BACK_MP_MENU in callbacks


def test_quick_interval_keyboard_has_common_options_and_custom():
    callbacks = [
        button.callback_data
        for row in media_pool._mp_quick_interval_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    ]

    for choice in ("15m", "30m", "1h", "3h", "6h", "12h", "24h"):
        assert f"{media_pool.CB_MP_QUICK_PREFIX}{choice}" in callbacks
    assert media_pool.CB_MP_CUSTOM_INTERVAL in callbacks


def test_successful_item_add_keeps_user_in_multi_item_add_flow(monkeypatch):
    async def scenario():
        class PoolCollection:
            async def find_one(self, query):
                assert query == {"user_id": 42, "chat_id": -1001234567890}
                return {"items": [{}, {}]}

        class FakeDatabase:
            def __getitem__(self, name):
                assert name == COL_MEDIA_POOLS
                return PoolCollection()

        save_item = AsyncMock()
        monkeypatch.setattr(media_pool, "add_to_media_pool", save_item)
        monkeypatch.setattr(
            media_pool, "get_db", AsyncMock(return_value=FakeDatabase())
        )

        message = SimpleNamespace(
            photo=[],
            video=None,
            document=None,
            audio=None,
            animation=None,
            voice=None,
            text="A sample post",
            caption=None,
            from_user=SimpleNamespace(id=42),
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={
            "mpwiz": {
                "mode": "add",
                "mp_chat_id": -1001234567890,
                "mp_chat_title": "Publisher Group",
            }
        })
        update = SimpleNamespace(message=message)

        state = await media_pool.recv_mp_item(update, context)

        assert state == media_pool.MP_ADD_ITEM
        save_item.assert_awaited_once()
        assert context.user_data["mpwiz"]["mp_chat_id"] == -1001234567890
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert media_pool.CB_MP_ADD_DONE in callbacks
        assert media_pool.CB_MP_ADD_SCHEDULE in callbacks

    asyncio.run(scenario())


def test_quick_interval_selection_saves_value_and_advances_to_first_run():
    async def scenario():
        query = SimpleNamespace(
            data=f"{media_pool.CB_MP_QUICK_PREFIX}6h",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"mpwiz": {"mp_chat_id": -1001234567890}})
        update = SimpleNamespace(callback_query=query)

        state = await media_pool.cb_mp_quick_interval(update, context)

        assert state == media_pool.MP_SET_FIRST_RUN
        assert context.user_data["mpwiz"]["interval_value"] == 6
        assert context.user_data["mpwiz"]["interval_unit"] == "hours"
        assert context.user_data["mpwiz"]["interval_label"] == "6 hours"
        query.edit_message_text.assert_awaited_once()
        assert "Choose the First Post Time" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())


def test_reset_only_affects_the_selected_pool(monkeypatch):
    async def scenario():
        reset = AsyncMock()
        summary = AsyncMock(return_value=("Pool stats", InlineKeyboardMarkup([])))
        monkeypatch.setattr(media_pool, "reset_media_pool", reset)
        monkeypatch.setattr(media_pool, "_mp_manage_summary", summary)
        monkeypatch.setattr(
            media_pool, "ensure_chat_access",
            AsyncMock(return_value=SimpleNamespace(title="Publisher Group")),
        )

        query = SimpleNamespace(
            get_bot=lambda: object(),
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={
            "mpwiz": {
                "mp_chat_id": -1001234567890,
                "mp_chat_title": "Publisher Group",
            }
        })

        state = await media_pool.cb_mp_reset_yes(
            SimpleNamespace(callback_query=query), context
        )

        assert state == media_pool.MP_MANAGE_ACTION
        reset.assert_awaited_once_with(42, -1001234567890)
        summary.assert_awaited_once_with(42, -1001234567890)

    asyncio.run(scenario())


def test_clear_only_deletes_selected_pool_and_pauses_its_schedule(monkeypatch):
    async def scenario():
        class PoolCollection:
            def __init__(self):
                self.deleted_filter = None

            async def delete_one(self, query):
                self.deleted_filter = query
                return SimpleNamespace(deleted_count=1)

        class PostsCollection:
            def __init__(self):
                self.update_filter = None
                self.update_doc = None

            async def update_many(self, query, update):
                self.update_filter = query
                self.update_doc = update
                return SimpleNamespace(modified_count=1)

        pools = PoolCollection()
        posts = PostsCollection()

        class FakeDatabase:
            def __getitem__(self, name):
                if name == COL_MEDIA_POOLS:
                    return pools
                if name == COL_POSTS:
                    return posts
                raise AssertionError(f"Unexpected collection: {name}")

        monkeypatch.setattr(
            media_pool, "get_db", AsyncMock(return_value=FakeDatabase())
        )
        query = SimpleNamespace(
            get_bot=lambda: object(),
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={
            "mpwiz": {"mp_chat_id": -1001234567890}
        })
        monkeypatch.setattr(
            media_pool, "ensure_chat_access",
            AsyncMock(return_value=SimpleNamespace(title="Publisher Group")),
        )

        state = await media_pool.cb_mp_clear_yes(
            SimpleNamespace(callback_query=query), context
        )

        assert state == media_pool.MP_MENU
        assert pools.deleted_filter == {
            "user_id": 42,
            "chat_id": -1001234567890,
        }
        assert posts.update_filter == {
            "user_id": 42,
            "chat_id": -1001234567890,
            "recurrence.type": "pool",
            "status": "pending",
        }
        assert posts.update_doc["$set"]["status"] == "paused"
        assert "mpwiz" not in context.user_data

    asyncio.run(scenario())


def test_media_pool_conversation_registers_group_and_management_states():
    conversation = media_pool.build_media_pool()

    assert media_pool.MP_CHAT_ID in conversation.states
    assert media_pool.MP_ADD_ITEM in conversation.states
    assert media_pool.MP_SET_INTERVAL_VALUE in conversation.states
    assert media_pool.MP_MANAGE_CHAT in conversation.states
    assert media_pool.MP_MANAGE_ACTION in conversation.states
