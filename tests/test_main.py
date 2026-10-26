import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from main import BotService, PACK_EMOJIS
from sticker_engine import Style


def test_start_welcome_matches_reference_copy():
    service = BotService()
    service.bot_username = "EmojiTitleBot"
    service.store = MagicMock()
    service.store.register_user.return_value = False
    message = SimpleNamespace(reply_text=AsyncMock())
    user = SimpleNamespace(id=123456)
    update = SimpleNamespace(effective_message=message, effective_user=user)
    context = SimpleNamespace(args=[])

    asyncio.run(service.cmd_start(update, context))

    message.reply_text.assert_awaited_once_with(
        "👋 Hello! I'm @EmojiTitleBot. I can convert any text into beautiful animated emoji text. "
        "Just send me your text and I will create a custom emoji pack for you!"
    )


def test_daily_reset_countdown_is_utc_and_rounds_up_minutes():
    at_hour = datetime(2026, 10, 26, 23, 0, 0, tzinfo=timezone.utc)
    at_seconds = datetime(2026, 10, 26, 23, 59, 35, tzinfo=timezone.utc)

    assert BotService._time_until_utc_reset(at_hour) == (1, 0)
    assert BotService._time_until_utc_reset(at_seconds) == (0, 1)
    at_last_second = datetime(2026, 10, 26, 23, 59, 59, tzinfo=timezone.utc)
    assert BotService._time_until_utc_reset(at_last_second) == (0, 1)


def test_quota_message_contains_reference_pro_and_invite_copy():
    service = BotService()
    service.bot_username = "EmojiTitleBot"
    now = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)

    text = service._limit_message(123456, now)

    assert "You have reached the limit of 3 packs per day." in text
    assert "You can create a new pack in 1 hours in 0 minutes." in text
    assert "With 💎 PRO subscription you get:" in text
    assert "- Unlimited pack creation" in text
    assert "- Your text as the pack title instead of bot signature" in text
    assert "Or invite a friend to get +1 free pack!" in text
    assert "https://t.me/EmojiTitleBot?start=ref_123456" in text


def test_editor_keyboard_has_four_setting_rows_and_create_action():
    keyboard = BotService._keyboard(Style(text="Hello").normalized())

    assert len(keyboard.inline_keyboard) == 5
    assert keyboard.inline_keyboard[0][1].text == "Raleway italic"
    assert keyboard.inline_keyboard[-1][0].text == "✅ Create Sticker Set"
    assert keyboard.inline_keyboard[-1][0].callback_data == "create"


def test_pack_configuration_matches_six_emoji_reference():
    assert len(PACK_EMOJIS) == 6
