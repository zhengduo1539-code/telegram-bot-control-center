import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram import InputMediaAnimation

from main import BotService, STICKER_EMOJI
from sticker_engine import Style, split_text_for_stickers


def test_prepare_stickers_uploads_variable_media_and_uses_returned_file_ids():
    service = BotService()
    segments = split_text_for_stickers("Hola")
    bot = MagicMock()
    bot.upload_sticker_file = AsyncMock(
        side_effect=[
            SimpleNamespace(file_id=f"telegram-file-{index}")
            for index in range(len(segments))
        ]
    )
    webm_files = [b"webm-data"] * len(segments)

    stickers = asyncio.run(service._prepare_stickers(bot, 12345, webm_files))

    assert len(stickers) == len(segments) == 2
    assert [sticker.sticker for sticker in stickers] == [
        f"telegram-file-{index}" for index in range(len(segments))
    ]
    assert [sticker.emoji_list for sticker in stickers] == [[STICKER_EMOJI]] * len(segments)
    assert all(sticker.format == "video" for sticker in stickers)
    assert bot.upload_sticker_file.await_count == len(segments)
    for index, call in enumerate(bot.upload_sticker_file.await_args_list, start=1):
        assert call.kwargs["user_id"] == 12345
        assert call.kwargs["sticker_format"] == "video"
        assert call.kwargs["sticker"].filename == f"text_{index}.webm"


def test_preview_setting_edits_existing_message_using_uploaded_file_id(monkeypatch):
    service = BotService()
    user = SimpleNamespace(id=12345)
    old_message = SimpleNamespace(message_id=100)
    query = SimpleNamespace(
        data="font:1",
        message=old_message,
        answer=AsyncMock(),
        edit_message_media=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query, effective_user=user)
    uploaded = SimpleNamespace(
        message_id=200,
        animation=SimpleNamespace(file_id="telegram-preview-file-id"),
        delete=AsyncMock(),
    )
    bot = MagicMock()
    bot.send_animation = AsyncMock(return_value=uploaded)
    context = SimpleNamespace(bot=bot)

    service.sessions[user.id] = Style(text="Hello").normalized()
    service.session_message_ids[user.id] = old_message.message_id
    service.store = MagicMock()
    service.store.get_count.return_value = 1
    monkeypatch.setattr("main.render_preview_gif", lambda _style: b"GIF89a-test-preview")

    asyncio.run(service.on_callback(update, context))

    bot.send_animation.assert_awaited_once()
    send_kwargs = bot.send_animation.await_args.kwargs
    assert send_kwargs["chat_id"] == user.id
    assert send_kwargs["animation"].filename == "animated-text-preview.gif"
    assert "caption" not in send_kwargs
    assert "reply_markup" not in send_kwargs
    uploaded.delete.assert_awaited_once()
    query.edit_message_media.assert_awaited_once()
    edit_kwargs = query.edit_message_media.await_args.kwargs
    assert isinstance(edit_kwargs["media"], InputMediaAnimation)
    assert edit_kwargs["media"].media == "telegram-preview-file-id"
    assert edit_kwargs["reply_markup"] is not None
    assert service.session_message_ids[user.id] == old_message.message_id
    assert service.sessions[user.id].font != "Monaco"
