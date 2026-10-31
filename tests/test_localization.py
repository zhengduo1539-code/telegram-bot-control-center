"""Tests for the 20-language catalog, native keyboard and persistence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database
from handlers import language as language_handler
from localization import (
    LANGUAGES,
    LANGUAGE_LABELS,
    TRANSLATIONS,
    language_button_pattern,
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

    assert len(labels) == 20
    assert all(len(row) == 2 for row in keyboard.keyboard)
    assert set(labels) == set(LANGUAGE_LABELS.values())
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


def test_selected_language_is_saved_and_temporary_keyboard_is_removed(monkeypatch):
    async def scenario():
        message = SimpleNamespace(
            text=LANGUAGE_LABELS["my"],
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"awaiting_language_choice": True},
            bot=SimpleNamespace(username="ScheduleBot"),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
        )
        save = AsyncMock()
        monkeypatch.setattr(language_handler, "set_user_language", save)

        await language_handler.handle_language_choice(update, context)

        save.assert_awaited_once_with(123, "my")
        assert context.user_data["language"] == "my"
        assert "awaiting_language_choice" not in context.user_data
        assert message.reply_text.await_count == 2
        from telegram import ReplyKeyboardRemove

        assert isinstance(
            message.reply_text.await_args_list[0].kwargs["reply_markup"],
            ReplyKeyboardRemove,
        )

    asyncio.run(scenario())
