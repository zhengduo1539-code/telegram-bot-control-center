"""Regression tests for the Media Pool onboarding and scoped management flow."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardMarkup

from database import COL_MEDIA_POOLS, COL_POSTS
from handlers import queue_manager as media_pool
from localization import LANGUAGES
from media_pool_localization import _EXTRA_ROWS, _KEYS, TRANSLATIONS, translate_media_pool


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


def test_media_pool_intro_keyboard_has_back_and_localized_continue():
    keyboard = media_pool._mp_intro_keyboard("my")
    buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
    ]

    from localization import translate

    assert len(buttons) == 2
    assert buttons[0].text == translate("my", "back")
    assert buttons[0].callback_data == media_pool.CB_MAIN_MENU
    assert buttons[1].text == translate_media_pool("my", "continue")
    assert buttons[1].callback_data == media_pool.CB_MP_CONTINUE


def test_media_pool_entry_shows_guide_and_continue_opens_existing_menu(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            media_pool, "resolve_user_language", AsyncMock(return_value="my")
        )
        monkeypatch.setattr(media_pool.chat_cleanup, "reset", lambda *_: None)
        monkeypatch.setattr(media_pool.chat_cleanup, "track", lambda *_: None)

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=88),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={},
            application=SimpleNamespace(bot_data={}),
        )
        update = SimpleNamespace(callback_query=query)

        state = await media_pool.enter_media_pool(update, context)

        assert state == media_pool.MP_INTRO
        assert context.user_data["active_media_pool"] is True
        intro_text = query.edit_message_text.await_args.args[0]
        assert intro_text == translate_media_pool("my", "onboarding")
        intro_keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        intro_callbacks = [
            button.callback_data
            for row in intro_keyboard.inline_keyboard
            for button in row
        ]
        assert media_pool.CB_MAIN_MENU in intro_callbacks
        assert media_pool.CB_MP_CONTINUE in intro_callbacks

        state = await media_pool.cb_mp_continue(update, context)

        assert state == media_pool.MP_MENU
        menu_text = query.edit_message_text.await_args.args[0]
        assert menu_text == translate_media_pool("my", "menu_intro")
        menu_keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        menu_callbacks = [
            button.callback_data
            for row in menu_keyboard.inline_keyboard
            for button in row
        ]
        for callback in (
            media_pool.CB_MP_ADD_ITEM,
            media_pool.CB_MP_SET_INTERVAL,
            media_pool.CB_MP_VIEW,
            media_pool.CB_MP_MANAGE,
        ):
            assert callback in menu_callbacks

    asyncio.run(scenario())


def test_media_pool_intro_back_returns_to_main_menu(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            media_pool, "resolve_user_language", AsyncMock(return_value="my")
        )
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "language": "my",
                "active_media_pool": True,
                "mpwiz": {"mp_chat_id": -1001234567890},
            },
            bot=None,
        )

        state = await media_pool.cb_mp_main_menu(
            SimpleNamespace(callback_query=query), context
        )

        from localization import translate

        assert state == media_pool.ConversationHandler.END
        assert "active_media_pool" not in context.user_data
        assert "mpwiz" not in context.user_data
        assert query.edit_message_text.await_args.args[0] == translate("my", "menu_title")
        keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert media_pool.CB_MEDIA_POOL in callbacks

    asyncio.run(scenario())


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
            "language": "en",
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
            from_user=SimpleNamespace(id=42),
            data=f"{media_pool.CB_MP_QUICK_PREFIX}6h",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"language": "en", "mpwiz": {"mp_chat_id": -1001234567890}})
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
            "language": "en",
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
        summary.assert_awaited_once_with(42, -1001234567890, "en")

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
            "language": "en",
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

    assert media_pool.MP_INTRO in conversation.states
    assert media_pool.MP_MENU in conversation.states
    assert media_pool.MP_CHAT_ID in conversation.states
    assert media_pool.MP_ADD_ITEM in conversation.states
    assert media_pool.MP_SET_INTERVAL_VALUE in conversation.states
    assert media_pool.MP_MANAGE_CHAT in conversation.states
    assert media_pool.MP_MANAGE_ACTION in conversation.states


def test_media_pool_catalog_covers_all_twenty_languages_with_matching_keys():
    assert set(TRANSLATIONS) == set(LANGUAGES)
    assert len(TRANSLATIONS) == 20
    expected = set(_KEYS) | {
        key for extra_catalog in _EXTRA_ROWS.values() for key in extra_catalog
    }
    assert len(expected) == len(_KEYS) + len(_EXTRA_ROWS["en"])
    for language, catalog in TRANSLATIONS.items():
        assert set(catalog) == expected, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values())


def test_media_pool_timezone_error_translation_covers_all_locales():
    assert set(_EXTRA_ROWS) == set(LANGUAGES)
    for language, catalog in _EXTRA_ROWS.items():
        assert catalog["invalid_timezone"].strip()
        assert translate_media_pool(language, "invalid_timezone") == catalog["invalid_timezone"]


def test_media_pool_navigation_buttons_use_selected_language():
    keyboard = media_pool._mp_nav_keyboard("my", back_data=media_pool.CB_BACK_MP_MENU)
    labels = _button_labels(keyboard)

    from localization import translate

    assert translate("my", "back") in labels
    assert translate("my", "cancel") in labels
    assert "⬅️ Back" not in labels
    assert "❌ Cancel" not in labels


def test_media_pool_and_queue_manager_use_separate_cancel_handlers():
    queue_conversation = media_pool.build_queue_manager()
    pool_conversation = media_pool.build_media_pool()

    assert queue_conversation.fallbacks[0].callback.__name__ == "cmd_cancel"
    assert pool_conversation.fallbacks[0].callback.__name__ == "cmd_mp_cancel"


def test_media_pool_translations_format_placeholders_and_fallback():
    assert "Publisher Group" in translate_media_pool(
        "my", "item_saved", group="Publisher Group", count=3
    )
    assert "Publisher Group" in translate_media_pool(
        "zh", "manage_summary",
        group="Publisher Group", total=10, remaining=4, published=6,
    )
    assert translate_media_pool("unknown-locale", "add_content") == TRANSLATIONS["en"]["add_content"]
    assert translate_media_pool("en", "not_a_key") == "not_a_key"


def test_media_pool_main_menu_and_quick_interval_are_localized():
    menu = _button_labels(media_pool._mp_menu_keyboard("my"))
    assert "➕ Content ထည့်ရန်" in menu
    assert "⏱ ပို့မည့်အချိန် သတ်မှတ်ရန်" in menu
    assert "📊 Pool အခြေအနေ" in menu
    assert "🛠 Pool စီမံရန်" in menu

    quick = _button_labels(media_pool._mp_quick_interval_keyboard("th"))
    assert "15 นาที" in quick
    assert "6 ชั่วโมง" in quick
    assert "✏️ กำหนดเอง" in quick


def test_media_pool_group_and_pool_management_keyboards_are_localized():
    group_keyboard = media_pool._mp_group_picker_keyboard(
        [{"chat_id": -1001234567890, "title": "Publisher Group", "chat_type": "supergroup"}],
        page=0,
        language="my",
    )
    group_labels = _button_labels(group_keyboard)
    assert "✏️ Chat ID ကို ကိုယ်တိုင်ထည့်ရန်" in group_labels

    manage_keyboard = media_pool._mp_manage_actions_keyboard("zh")
    manage_labels = _button_labels(manage_keyboard)
    assert "🔄 重置发布周期" in manage_labels
    assert "🗑 清除此内容池" in manage_labels


def test_media_pool_timezone_keyboard_uses_stable_timezone_identifiers_and_local_navigation():
    keyboard = media_pool._mp_tz_keyboard(page=0, language="my")
    labels = _button_labels(keyboard)
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "UTC" in labels
    assert any("Asia/" in label for label in labels)
    assert media_pool.CB_BACK_MP_FR in callbacks
    assert all(button.text != "◀️ Prev" for row in keyboard.inline_keyboard for button in row)


def test_manual_chat_id_help_and_cancel_feedback_use_selected_language(monkeypatch):
    async def scenario():
        cleanup = AsyncMock()
        monkeypatch.setattr(media_pool.chat_cleanup, "cleanup", cleanup)

        prompt = translate_media_pool("my", "manual_id_prompt")
        assert "/id" in prompt
        assert "Chat ID" in prompt

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=88),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"language": "my", "active_media_pool": True},
            bot=SimpleNamespace(username="ScheduleBot"),
        )
        update = SimpleNamespace(callback_query=query)

        state = await media_pool.cb_cancel_local(update, context)

        assert state == media_pool.ConversationHandler.END
        text = query.edit_message_text.await_args.args[0]
        assert "ပယ်ဖျက်" in text
        assert "active_media_pool" not in context.user_data
        cleanup.assert_awaited_once()

    asyncio.run(scenario())
