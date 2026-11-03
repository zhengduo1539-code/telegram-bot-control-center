"""Tests for the 20-language catalog, native keyboard and persistence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

import database
from handlers import language as language_handler
from localization import (
    LANGUAGES,
    LANGUAGE_LABELS,
    TRANSLATIONS,
    language_button_pattern,
    language_cancel_button_label,
    language_from_button,
    language_keyboard,
    translate,
)


def test_exactly_twenty_languages_have_complete_core_catalogs():
    assert len(LANGUAGES) == 20
    required = set(TRANSLATIONS["en"])
    assert set(TRANSLATIONS) == set(LANGUAGES)
    for code, catalog in TRANSLATIONS.items():
        assert set(catalog) == required, code
        assert all(isinstance(value, str) and value for value in catalog.values())


def test_language_reply_keyboard_has_twenty_buttons_in_two_columns():
    keyboard = language_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]

    assert len(labels) == 21
    assert all(len(row) == 2 for row in keyboard.keyboard[:-1])
    assert keyboard.keyboard[-1][0].text == language_cancel_button_label("en")
    assert len(keyboard.keyboard[-1]) == 1
    assert set(labels[:-1]) == set(LANGUAGE_LABELS.values())
    assert keyboard.resize_keyboard is True
    assert keyboard.one_time_keyboard is True
    assert keyboard.is_persistent is False


def test_language_button_mapping_and_regex_are_exact():
    pattern = language_button_pattern()
    import re

    for code, label in LANGUAGE_LABELS.items():
        assert language_from_button(label) == code
        assert re.fullmatch(pattern, label)
    assert language_from_button("English") is None
    assert language_from_button("🌐 Unknown") is None
    assert re.fullmatch(pattern, language_cancel_button_label("en"))


def test_translation_interpolates_group_name_and_falls_back_safely():
    assert "Publisher Group" in translate("my", "group_start_body", group="Publisher Group")
    assert translate("unknown", "menu_title") == translate("en", "menu_title")
    assert translate("my", "not_a_real_key") == "not_a_real_key"


class FakeUsersCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, projection=None):
        return self.docs.get(query["user_id"])

    async def update_one(self, query, update, upsert=False):
        user_id = query["user_id"]
        is_new = user_id not in self.docs
        doc = self.docs.setdefault(user_id, {"user_id": user_id})
        if is_new:
            doc.update(update.get("$setOnInsert", {}))
        doc.update(update.get("$set", {}))


class FakeDatabase:
    def __init__(self):
        self.users = FakeUsersCollection()

    def __getitem__(self, collection):
        assert collection == database.COL_USERS
        return self.users


def test_language_preference_round_trips_without_overwriting_profile(monkeypatch):
    async def scenario():
        db = FakeDatabase()

        async def fake_get_db():
            return db

        monkeypatch.setattr(database, "get_db", fake_get_db)
        assert await database.get_user_language(555) == "en"

        db.users.docs[555] = {
            "user_id": 555,
            "username": "test_user",
            "first_name": "Test",
        }
        await database.set_user_language(555, "my")
        assert await database.get_user_language(555) == "my"
        assert db.users.docs[555]["username"] == "test_user"
        assert db.users.docs[555]["first_name"] == "Test"

        with pytest.raises(ValueError):
            await database.set_user_language(555, "xx")

    asyncio.run(scenario())


def test_lang_command_displays_the_reply_keyboard_in_private_chat(monkeypatch):
    async def scenario():
        message = SimpleNamespace(reply_text=AsyncMock(), text="/lang")
        context = SimpleNamespace(user_data={}, bot=object())
        update = SimpleNamespace(
            callback_query=None,
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            language_handler, "get_user_language", AsyncMock(return_value="en")
        )

        await language_handler.cmd_lang(update, context)

        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        assert len([b for row in keyboard.keyboard for b in row]) == 20
        assert context.user_data["awaiting_language_choice"] is True

    asyncio.run(scenario())


def test_language_picker_exit_returns_to_main_menu_without_changing_saved_locale():
    async def scenario():
        user_id = 123456
        chat_id = user_id
        menu_message = SimpleNamespace(reply_text=AsyncMock())
        selected_message = SimpleNamespace(
            text=language_cancel_button_label("en"),
            chat_id=chat_id,
            message_id=777,
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"language": "en", "awaiting_language_choice": True},
            bot_data={},
            bot=SimpleNamespace(
                delete_message=AsyncMock(return_value=True),
            ),
        )
        update = SimpleNamespace(
            effective_message=selected_message,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
        )

        await language_handler.handle_language_choice(update, context)

        assert context.user_data["language"] == "en"
        assert "awaiting_language_choice" not in context.user_data
        context.bot.delete_message.assert_awaited_once_with(
            chat_id=chat_id, message_id=777
        )
        assert selected_message.reply_text.await_count == 1
        sent = selected_message.reply_text.await_args.kwargs
        assert isinstance(sent["reply_markup"], InlineKeyboardMarkup)
        assert sent["text"] == translate("en", "menu_title")

    asyncio.run(scenario())


def test_group_lang_command_uses_reply_keyboard_and_clickable_private_link():
    async def scenario():
        chat_id = -1001234567890
        sent = SimpleNamespace(message_id=901)
        message = SimpleNamespace(reply_text=AsyncMock(return_value=sent))
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="ScheduleBot"))
        )
        context = SimpleNamespace(bot=bot, bot_data={}, user_data={})
        update = SimpleNamespace(
            callback_query=None,
            effective_message=message,
            effective_chat=SimpleNamespace(type="supergroup", id=chat_id),
            effective_user=SimpleNamespace(id=123),
        )

        await language_handler.cmd_lang(update, context)

        kwargs = message.reply_text.await_args.kwargs
        assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)
        assert [[button.text for button in row] for row in kwargs["reply_markup"].keyboard] == [
            ["📩 Open Bot in Private Chat"]
        ]
        assert "https://t.me/ScheduleBot" in message.reply_text.await_args.args[0]
        route = context.bot_data["_reply_keyboard_route_registry"][str(chat_id)]["routes"]
        assert route["📩 Open Bot in Private Chat"] == {
            "type": "url",
            "url": "https://t.me/ScheduleBot",
        }

    asyncio.run(scenario())


def test_selected_language_is_saved_and_temporary_keyboard_is_removed(monkeypatch):
    async def scenario():
        message = SimpleNamespace(
            text=LANGUAGE_LABELS["my"],
            chat_id=123456789,
            message_id=77,
            reply_text=AsyncMock(),
        )
        bot = SimpleNamespace(
            username="ScheduleBot",
            delete_message=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(
            user_data={"awaiting_language_choice": True},
            bot=bot,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=123),
        )
        save = AsyncMock()
        monkeypatch.setattr(language_handler, "set_user_language", save)

        await language_handler.handle_language_choice(update, context)

        save.assert_awaited_once_with(123, "my")
        bot.delete_message.assert_awaited_once_with(chat_id=123456789, message_id=77)
        assert context.user_data["language"] == "my"
        assert "awaiting_language_choice" not in context.user_data
        assert message.reply_text.await_count == 2
        from telegram import ReplyKeyboardRemove

        assert isinstance(
            message.reply_text.await_args_list[0].kwargs["reply_markup"],
            ReplyKeyboardRemove,
        )

    asyncio.run(scenario())


def test_main_menu_and_my_posts_controls_use_the_selected_locale():
    from handlers.base import _posts_keyboard, main_menu_keyboard

    menu = main_menu_keyboard(user_id=987654321, language="my")
    menu_labels = [
        button.text
        for row in menu.inline_keyboard
        for button in row
    ]
    assert "📅 Post အသစ် Schedule" in menu_labels
    assert "🌐 ဘာသာစကား" in menu_labels
    assert not any("Template" in label or "テンプレート" in label for label in menu_labels)

    posts = [{
        "_id": "0123456789abcdef01234567",
        "status": "pending",
        "chat_id": -1001234567890,
        "recurrence": {"type": "once"},
        "timezone": "Asia/Yangon",
    }]
    controls = _posts_keyboard(posts, 0, "my")
    labels = [
        button.text
        for row in controls.inline_keyboard
        for button in row
    ]
    assert "⏸ ခေတ္တရပ် #1" in labels
    assert "🗑 ဖျက်ရန် #1" in labels
    assert not any("Template" in label or "テンプレート" in label for label in labels)




def test_main_menu_only_exposes_active_user_actions_in_all_locales():
    from handlers.base import main_menu_keyboard

    for language in LANGUAGES:
        menu = main_menu_keyboard(language=language)
        callback_data = {
            button.callback_data
            for row in menu.inline_keyboard
            for button in row
            if button.callback_data
        }
        assert callback_data == {
            "action:schedule_new",
            "action:my_posts",
            "nav:language",
        }, language
