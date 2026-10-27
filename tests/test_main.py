import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.constants import ChatType

from main import BotService, STICKER_EMOJI
from sticker_engine import CUSTOM_EMOJI_COUNT, Style


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


def test_editor_keyboard_matches_reference_arrows_and_create_action():
    keyboard = BotService._keyboard(Style(text="Hello").normalized())

    assert len(keyboard.inline_keyboard) == 5
    assert keyboard.inline_keyboard[0][1].text == "Raleway italic"
    for row in keyboard.inline_keyboard[:4]:
        assert row[0].text == "👉🏻"
        assert row[2].text == "👉🏻"
    assert keyboard.inline_keyboard[-1][0].text == "✅ Create Emoji Pack"
    assert keyboard.inline_keyboard[-1][0].callback_data == "create"

    loading = BotService._keyboard(Style(text="Hello").normalized(), creating=True)
    assert loading.inline_keyboard[-1][0].text == "⌛ Creating Emoji Pack..."


def test_pack_configuration_uses_three_custom_emoji_variants():
    assert STICKER_EMOJI == "✨"
    assert CUSTOM_EMOJI_COUNT == 3


def test_prepare_stickers_uploads_three_custom_emoji_assets():
    service = BotService()
    bot = SimpleNamespace(
        upload_sticker_file=AsyncMock(side_effect=[
            SimpleNamespace(file_id="file-one"),
            SimpleNamespace(file_id="file-two"),
            SimpleNamespace(file_id="file-three"),
        ])
    )

    stickers = asyncio.run(service._prepare_stickers(
        bot, 123456, [b"webm-one", b"webm-two", b"webm-three"]
    ))

    assert len(stickers) == 3
    assert [sticker.sticker for sticker in stickers] == ["file-one", "file-two", "file-three"]
    assert [sticker.emoji_list for sticker in stickers] == [("✨",), ("✨",), ("✨",)]
    assert all(sticker.format == "video" for sticker in stickers)


def test_new_text_reuses_the_last_selected_appearance():
    service = BotService()
    service.bot_username = "EmojiTitleBot"
    service.store = MagicMock()
    service.store.register_user.return_value = False
    service.store.get_count.return_value = 0
    service.last_styles[123456] = Style(
        text="Previous", font="Monaco", color="Rainbow", shape="shape_2", animation="anim_2"
    )

    message = SimpleNamespace(
        text="OK",
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        reply_text=AsyncMock(),
        reply_animation=AsyncMock(return_value=SimpleNamespace(message_id=42)),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123456),
    )
    context = SimpleNamespace(bot=SimpleNamespace(), args=[])

    with patch("main.render_preview_gif", return_value=b"GIF89a-test"):
        asyncio.run(service.on_text(update, context))

    selected = service.sessions[123456]
    assert selected.text == "OK"
    assert selected.font == "Monaco"
    assert selected.color == "Rainbow"
    assert selected.shape == "shape_2"
    assert selected.animation == "anim_2"


def test_selector_icons_match_reference_choices_and_arrows_are_identical():
    default_style = Style(text="Hello").normalized()
    keyboard = BotService._keyboard(default_style)
    for row in keyboard.inline_keyboard[:4]:
        assert row[0].text == "👉🏻"
        assert row[2].text == "👉🏻"
    assert keyboard.inline_keyboard[1][1].text == "💊"  # Orange
    assert BotService._keyboard(Style(text="x", color="Pink").normalized()).inline_keyboard[1][1].text == "💋"
    assert BotService._keyboard(Style(text="x", color="Lime").normalized()).inline_keyboard[1][1].text == "🟢🟡"
    assert BotService._keyboard(Style(text="x", color="Rainbow").normalized()).inline_keyboard[1][1].text == "🌈"
    assert keyboard.inline_keyboard[2][1].text == "🎟️"
    assert BotService._keyboard(Style(text="x", shape="shape_1").normalized()).inline_keyboard[2][1].text == "🟣"
    assert BotService._keyboard(Style(text="x", shape="shape_2").normalized()).inline_keyboard[2][1].text == "💜"
    assert keyboard.inline_keyboard[3][1].text == "🚩"
    assert BotService._keyboard(Style(text="x", animation="anim_1").normalized()).inline_keyboard[3][1].text == "🚫"
    assert BotService._keyboard(Style(text="x", animation="anim_2").normalized()).inline_keyboard[3][1].text == "⬇️"


def test_new_text_loads_saved_appearance_after_service_restart(monkeypatch):
    service = BotService()
    service.bot_username = "EmojiTitleBot"
    service.store = MagicMock()
    service.store.register_user.return_value = False
    service.store.get_style.return_value = {
        "font": "Monaco",
        "color": "Rainbow",
        "shape": "Heart",
        "animation": "Pulse",
    }
    service.store.get_count.return_value = 0
    message = SimpleNamespace(
        text="New",
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        reply_text=AsyncMock(),
        reply_animation=AsyncMock(return_value=SimpleNamespace(message_id=42)),
    )
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=123456))
    context = SimpleNamespace(bot=SimpleNamespace(), args=[])

    with patch("main.render_preview_gif", return_value=b"GIF89a-test"):
        asyncio.run(service.on_text(update, context))

    selected = service.sessions[123456]
    assert selected.text == "New"
    assert (selected.font, selected.color, selected.shape, selected.animation) == (
        "Monaco", "Rainbow", "shape_2", "anim_0"
    )
    service.store.save_style.assert_called_once_with(123456, {
        "font": "Monaco", "color": "Rainbow", "shape": "shape_2", "animation": "anim_0"
    })
