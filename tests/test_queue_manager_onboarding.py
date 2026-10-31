"""Regression tests for the Queue Manager onboarding and guided workflow."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import queue_manager as queue
from localization import LANGUAGES, translate
from queue_manager_localization import _MESSAGES, translate_queue_manager


def _buttons(keyboard):
    return [
        button
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_queue_manager_translation_catalog_covers_all_languages_and_keys():
    assert set(_MESSAGES) == set(LANGUAGES)
    expected = set(_MESSAGES["en"])
    assert expected >= {
        "intro", "continue", "menu", "slots_review", "item_review",
        "clear_confirm", "view_empty",
    }
    for language, catalog in _MESSAGES.items():
        assert set(catalog) == expected, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values())

    review = translate_queue_manager(
        "my",
        "slots_review",
        group="Publisher Group",
        slots="09:00 • 14:00",
        timezone="Asia/Bangkok",
    )
    assert "Publisher Group" in review
    assert "09:00 • 14:00" in review
    assert "Asia/Bangkok" in review


def test_queue_intro_keyboard_has_back_and_localized_continue():
    buttons = _buttons(queue._qm_intro_keyboard("my"))

    assert len(buttons) == 2
    assert buttons[0].text == translate("my", "back")
    assert buttons[0].callback_data == queue.CB_MAIN_MENU
    assert buttons[1].text == translate_queue_manager("my", "continue")
    assert buttons[1].callback_data == queue.CB_QM_CONTINUE


def test_queue_entry_continue_and_back_follow_expected_navigation(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            queue, "resolve_user_language", AsyncMock(return_value="my")
        )
        monkeypatch.setattr(queue.chat_cleanup, "reset", lambda *_: None)
        monkeypatch.setattr(queue.chat_cleanup, "track", lambda *_: None)

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=88),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={},
            application=SimpleNamespace(bot_data={}),
            bot=None,
        )
        update = SimpleNamespace(callback_query=query)

        state = await queue.enter_queue_manager(update, context)

        assert state == queue.QM_INTRO
        assert context.user_data["active_queue_manager"] is True
        intro_text = query.edit_message_text.await_args.args[0]
        assert intro_text == translate_queue_manager("my", "intro")
        assert "FIFO" in intro_text
        intro_callbacks = [
            button.callback_data
            for button in _buttons(
                query.edit_message_text.await_args.kwargs["reply_markup"]
            )
        ]
        assert intro_callbacks == [queue.CB_MAIN_MENU, queue.CB_QM_CONTINUE]

        state = await queue.cb_qm_continue(update, context)

        assert state == queue.QM_MENU
        assert query.edit_message_text.await_args.args[0] == translate_queue_manager(
            "my", "menu"
        )
        menu_callbacks = {
            button.callback_data
            for button in _buttons(
                query.edit_message_text.await_args.kwargs["reply_markup"]
            )
        }
        assert {
            queue.CB_QM_SET_SLOTS,
            queue.CB_QM_ADD_CONTENT,
            queue.CB_QM_VIEW,
            queue.CB_QM_CLEAR,
        } <= menu_callbacks

        state = await queue.cb_qm_back_main_menu(update, context)

        assert state == queue.ConversationHandler.END
        assert "active_queue_manager" not in context.user_data
        assert "qwiz" not in context.user_data
        assert query.edit_message_text.await_args.args[0] == translate(
            "my", "menu_title"
        )

    asyncio.run(scenario())


def test_daily_slots_timezone_selection_reviews_without_writing_until_confirm(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            queue, "resolve_user_language", AsyncMock(return_value="my")
        )
        save_slots = AsyncMock()
        monkeypatch.setattr(queue, "upsert_queue_slots", save_slots)
        monkeypatch.setattr(
            queue.chat_cleanup, "cleanup", AsyncMock()
        )
        monkeypatch.setattr(
            queue, "ensure_chat_access",
            AsyncMock(return_value=SimpleNamespace(title="Publisher Group")),
        )

        context = SimpleNamespace(
            user_data={
                "language": "my",
                "qwiz": {
                    "slots_chat_id": -1001234567890,
                    "slots_chat_title": "Publisher Group",
                    "pending_slots": ["09:00", "14:00"],
                },
            },
            bot=None,
        )
        timezone_query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            data="qmtz:Asia/Bangkok",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )

        state = await queue.cb_qm_tz(
            SimpleNamespace(callback_query=timezone_query), context
        )

        assert state == queue.QM_CONFIRM_SLOTS
        assert context.user_data["qwiz"]["pending_timezone"] == "Asia/Bangkok"
        save_slots.assert_not_awaited()
        review_text = timezone_query.edit_message_text.await_args.args[0]
        assert review_text == translate_queue_manager(
            "my",
            "slots_review",
            group="Publisher Group",
            slots="09:00 • 14:00",
            timezone="GMT+7:00 Thailand",
        )
        review_callbacks = {
            button.callback_data
            for button in _buttons(
                timezone_query.edit_message_text.await_args.kwargs["reply_markup"]
            )
        }
        assert queue.CB_QM_CONFIRM_SLOTS in review_callbacks
        assert queue.CB_BACK_QM_SLOT_TZ in review_callbacks

        confirm_query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=99),
            get_bot=lambda: object(),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        state = await queue.cb_qm_confirm_slots(
            SimpleNamespace(callback_query=confirm_query), context
        )

        assert state == queue.QM_MENU
        save_slots.assert_awaited_once_with(
            42, -1001234567890, ["09:00", "14:00"], "Asia/Bangkok"
        )
        assert "qwiz" not in context.user_data
        assert "နေ့စဉ် Schedule သိမ်းဆည်းပြီးပါပြီ" in (
            confirm_query.edit_message_text.await_args.args[0]
        )
        queue.chat_cleanup.cleanup.assert_awaited_once()

    asyncio.run(scenario())


def test_content_is_reviewed_as_one_fifo_item_before_it_is_added(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            queue, "resolve_user_language", AsyncMock(return_value="my")
        )
        queue_doc = {
            "slots": ["09:00", "14:00"],
            "timezone": "Asia/Bangkok",
            "next_run_at": None,
        }
        get_queue = AsyncMock(return_value=queue_doc)
        add_to_queue = AsyncMock(return_value="abcdef123456")
        ensure_access = AsyncMock(
            return_value=SimpleNamespace(title="Publisher Group")
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(queue, "get_queue", get_queue)
        monkeypatch.setattr(queue, "add_to_queue", add_to_queue)
        monkeypatch.setattr(queue, "ensure_chat_access", ensure_access)
        monkeypatch.setattr(queue.chat_cleanup, "cleanup", cleanup)

        message = SimpleNamespace(
            photo=[],
            video=None,
            document=None,
            audio=None,
            animation=None,
            voice=None,
            text="My first queued post",
            caption=None,
            from_user=SimpleNamespace(id=42),
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "language": "my",
                "qwiz": {
                    "add_chat_id": -1001234567890,
                    "add_chat_title": "Publisher Group",
                },
            },
            bot=object(),
        )

        state = await queue.recv_qm_content(
            SimpleNamespace(message=message), context
        )

        assert state == queue.QM_CONFIRM_SLOT
        review_text = message.reply_text.await_args.args[0]
        assert "Publisher Group" in review_text
        assert "My first queued post" in review_text
        assert "09:00 • 14:00" in review_text
        assert "GMT+7:00 Thailand" in review_text
        add_to_queue.assert_not_awaited()
        review_callbacks = {
            button.callback_data
            for button in _buttons(
                message.reply_text.await_args.kwargs["reply_markup"]
            )
        }
        assert queue.CB_QM_CONFIRM_ADD in review_callbacks
        assert queue.CB_BACK_QM_AC_CONTENT in review_callbacks

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=100),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        state = await queue.cb_qm_confirm_add(
            SimpleNamespace(callback_query=query), context
        )

        assert state == queue.QM_MENU
        add_to_queue.assert_awaited_once_with(
            42,
            -1001234567890,
            {
                "text": "My first queued post",
                "media_file_id": None,
                "media_type": None,
            },
        )
        assert "qwiz" not in context.user_data
        assert "Daily Queue ထဲသို့ ထည့်သွင်းပြီးပါပြီ။" in (
            query.edit_message_text.await_args.args[0]
        )
        cleanup.assert_awaited_once()

    asyncio.run(scenario())


def test_clear_queue_warning_explains_global_scope_and_preserves_published_posts():
    async def scenario():
        monkeypatch_resolve = AsyncMock(return_value="en")
        original = queue.resolve_user_language
        queue.resolve_user_language = monkeypatch_resolve
        try:
            query = SimpleNamespace(
                from_user=SimpleNamespace(id=42),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            context = SimpleNamespace(user_data={"language": "en"})
            state = await queue.cb_qm_clear(
                SimpleNamespace(callback_query=query), context
            )
            assert state == queue.QM_MENU
            text = query.edit_message_text.await_args.args[0]
            assert "all queue records" in text.lower()
            assert "every group and channel linked to your account" in text.lower()
            assert "already published" in text.lower()
            callbacks = {
                button.callback_data
                for button in _buttons(
                    query.edit_message_text.await_args.kwargs["reply_markup"]
                )
            }
            assert queue.CB_QM_CLEAR_YES in callbacks
            assert queue.CB_BACK_QM_MENU in callbacks
        finally:
            queue.resolve_user_language = original

    asyncio.run(scenario())


def test_queue_conversation_registers_intro_review_and_localized_cancel():
    conversation = queue.build_queue_manager()

    assert queue.QM_INTRO in conversation.states
    assert queue.QM_MENU in conversation.states
    assert queue.QM_CONFIRM_SLOTS in conversation.states
    assert conversation.fallbacks[0].callback.__name__ == "cmd_qm_cancel"
    assert any(
        handler.callback.__name__ == "cb_qm_continue"
        for handler in conversation.states[queue.QM_INTRO]
    )
    assert any(
        handler.callback.__name__ == "cb_qm_confirm_slots"
        for handler in conversation.states[queue.QM_CONFIRM_SLOTS]
    )
    assert any(
        handler.pattern.match(queue.CB_MAIN_MENU)
        for handler in conversation.states[queue.QM_INTRO]
    )


def test_queue_cancel_command_uses_saved_language_and_returns_main_menu(monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            queue, "resolve_user_language", AsyncMock(return_value="my")
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(queue.chat_cleanup, "cleanup", cleanup)

        message = SimpleNamespace(
            chat_id=42,
            message_id=101,
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "active_queue_manager": True,
                "qwiz": {"slots_chat_id": -1001234567890},
            },
            bot=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42),
        )

        state = await queue.cmd_qm_cancel(update, context)

        assert state == queue.ConversationHandler.END
        assert "active_queue_manager" not in context.user_data
        assert "qwiz" not in context.user_data
        assert "ပယ်ဖျက်" in message.reply_text.await_args.args[0]
        cleanup.assert_awaited_once()

    asyncio.run(scenario())
