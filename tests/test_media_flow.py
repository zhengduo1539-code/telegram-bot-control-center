import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram import InputMediaAnimation, MessageEntity, Sticker

from main import BotService, STICKER_EMOJI
from sticker_engine import CUSTOM_EMOJI_COUNT, Style


def test_prepare_stickers_uploads_three_custom_emoji_assets_and_uses_returned_file_ids():
    service = BotService()
    bot = MagicMock()
    bot.upload_sticker_file = AsyncMock(
        side_effect=[
            SimpleNamespace(file_id=f"telegram-file-{index}")
            for index in range(CUSTOM_EMOJI_COUNT)
        ]
    )
    webm_files = [b"webm-data"] * CUSTOM_EMOJI_COUNT

    stickers = asyncio.run(service._prepare_stickers(bot, 12345, webm_files))

    assert len(stickers) == CUSTOM_EMOJI_COUNT == 3
    assert [sticker.sticker for sticker in stickers] == [
        f"telegram-file-{index}" for index in range(CUSTOM_EMOJI_COUNT)
    ]
    assert [sticker.emoji_list for sticker in stickers] == [(STICKER_EMOJI,)] * CUSTOM_EMOJI_COUNT
    assert all(sticker.format == "video" for sticker in stickers)
    assert bot.upload_sticker_file.await_count == CUSTOM_EMOJI_COUNT
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


def test_create_pack_uses_custom_emoji_type_and_addemoji_link(monkeypatch):
    service = BotService()
    user_id = 123456
    service.bot_username = "EmojiTitleBot"
    service.status = "running"
    service.store = MagicMock()
    service.store.get_account.return_value = {"is_pro": False}
    service.store.reserve.return_value = 1
    service.store.get_count.return_value = 1

    query_message = SimpleNamespace(reply_text=AsyncMock(), message_id=90)
    query = SimpleNamespace(
        data="create",
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=query_message,
    )
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id))
    bot = SimpleNamespace(
        upload_sticker_file=AsyncMock(side_effect=[
            SimpleNamespace(file_id="emoji-file-one"),
            SimpleNamespace(file_id="emoji-file-two"),
            SimpleNamespace(file_id="emoji-file-three"),
        ]),
        create_new_sticker_set=AsyncMock(),
        get_sticker_set=AsyncMock(return_value=SimpleNamespace(
            stickers=[SimpleNamespace(custom_emoji_id="custom-emoji-id")]
        )),
        send_message=AsyncMock(),
    )
    context = SimpleNamespace(bot=bot)
    service.sessions[user_id] = Style(text="Hello").normalized()
    monkeypatch.setattr("main.render_pack_webm", lambda _style: [b"webm-one", b"webm-two", b"webm-three"])

    asyncio.run(service._create_pack(update, context))

    create_kwargs = bot.create_new_sticker_set.await_args.kwargs
    assert create_kwargs["sticker_type"] == Sticker.CUSTOM_EMOJI
    assert create_kwargs["needs_repainting"] is False
    assert len(create_kwargs["stickers"]) == 3
    assert bot.send_message.await_args.kwargs["entities"][0].type == MessageEntity.CUSTOM_EMOJI
    confirmation = query_message.reply_text.await_args.args[0]
    assert "https://t.me/addemoji/" in confirmation
    assert "Emojipack with 3 emoji" in confirmation
