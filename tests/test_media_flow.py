import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from main import BotService, PACK_EMOJIS
from sticker_engine import Style


def test_prepare_stickers_uploads_media_and_uses_returned_file_ids():
    service = BotService()
    bot = MagicMock()
    bot.upload_sticker_file = AsyncMock(
        side_effect=[
            SimpleNamespace(file_id=f"telegram-file-{index}")
            for index in range(len(PACK_EMOJIS))
        ]
    )
    webm_files = [b"webm-data"] * len(PACK_EMOJIS)

    stickers = asyncio.run(service._prepare_stickers(bot, 12345, webm_files))

    assert len(stickers) == len(PACK_EMOJIS)
    assert [sticker.sticker for sticker in stickers] == [
        f"telegram-file-{index}" for index in range(len(PACK_EMOJIS))
    ]
    assert [sticker.emoji_list for sticker in stickers] == [[emoji] for emoji in PACK_EMOJIS]
    assert all(sticker.format == "video" for sticker in stickers)
    assert bot.upload_sticker_file.await_count == len(PACK_EMOJIS)
    for index, call in enumerate(bot.upload_sticker_file.await_args_list, start=1):
        assert call.kwargs["user_id"] == 12345
        assert call.kwargs["sticker_format"] == "video"
        assert call.kwargs["sticker"].filename == f"emoji_{index}.webm"


def test_preview_setting_sends_new_animation_and_removes_old_message(monkeypatch):
    service = BotService()
    user = SimpleNamespace(id=12345)
    old_message = SimpleNamespace(message_id=100, delete=AsyncMock())
    query = SimpleNamespace(
        data="font:1",
        message=old_message,
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query, effective_user=user)
    bot = MagicMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=200))
    context = SimpleNamespace(bot=bot)

    service.sessions[user.id] = Style(text="Hello").normalized()
    service.session_message_ids[user.id] = old_message.message_id
    service.store = MagicMock()
    service.store.get_count.return_value = 1
    monkeypatch.setattr("main.render_preview_gif", lambda _style: b"GIF89a-test-preview")

    asyncio.run(service.on_callback(update, context))

    bot.send_animation.assert_awaited_once()
    assert bot.send_animation.await_args.kwargs["chat_id"] == user.id
    assert bot.send_animation.await_args.kwargs["animation"].filename == "animated-text-preview.gif"
    old_message.delete.assert_awaited_once()
    assert service.session_message_ids[user.id] == 200
    assert service.sessions[user.id].font != "Impact"
