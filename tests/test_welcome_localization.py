"""Regression tests for the localized /start welcome message."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from handlers.base import MAIN_MENU, cmd_start
from localization import LANGUAGES, translate
from welcome_localization import WELCOME_MESSAGES, welcome_text


def test_welcome_messages_cover_all_twenty_supported_locales():
    assert set(WELCOME_MESSAGES) == set(LANGUAGES)
    for language in LANGUAGES:
        message = welcome_text(language)
        assert message.strip()
        assert "template" not in message.lower()
        assert "📝" not in message
        assert translate(language, "schedule_new") in message
        assert translate(language, "help_title") in message
        assert translate(language, "language") in message
        assert "template" not in translate(language, "help_text").lower()
        assert "Welcome to Schedule Bot" not in message if language != "en" else True


@pytest.mark.parametrize("language", tuple(LANGUAGES))
def test_private_start_displays_welcome_and_menu_in_selected_locale(language):
    async def scenario():
        message = SimpleNamespace(reply_text=AsyncMock())
        bot = SimpleNamespace(username="ScheduleBot")
        context = SimpleNamespace(bot=bot, args=[], user_data={})
        update = SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(type="private", id=123456789, title=None),
            effective_user=SimpleNamespace(id=123456789),
        )

        with patch(
            "handlers.base.resolve_user_language",
            new=AsyncMock(return_value=language),
        ):
            state = await cmd_start(update, context)

        assert state == MAIN_MENU
        body = message.reply_text.await_args.args[0]
        assert welcome_text(language) in body
        assert translate(language, "menu_title") in body
        assert message.reply_text.await_args.kwargs["reply_markup"] is not None

    asyncio.run(scenario())
