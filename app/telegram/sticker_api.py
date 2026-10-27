"""Adapter around Telegram Bot API sticker-set methods."""
from __future__ import annotations

from dataclasses import dataclass

from telegram import InputSticker

from naming import make_pack_name


@dataclass(frozen=True, slots=True)
class PublishedPack:
    name: str
    title: str
    pack_type: str
    link: str


class TelegramStickerPublisher:
    """Keep Telegram-specific publishing details out of the rendering engine."""

    async def create_pack(
        self,
        *,
        bot,
        owner_id: int,
        bot_username: str,
        text: str,
        assets: list[bytes],
        pack_type: str,
    ) -> PublishedPack:
        if pack_type not in {"custom_emoji", "regular"}:
            raise ValueError("Unsupported Telegram sticker-pack type.")
        if not assets:
            raise ValueError("Cannot publish an empty sticker pack.")

        slug_source = text if pack_type == "custom_emoji" else f"Sticker_{text}"
        name = make_pack_name(slug_source, bot_username)
        title_suffix = "Custom Emojis" if pack_type == "custom_emoji" else "Animated Stickers"
        title = f"{text} • {title_suffix}"[:64]
        stickers = [
            InputSticker(sticker=data, emoji_list=["✨"], format="animated")
            for data in assets
        ]

        await bot.create_new_sticker_set(
            user_id=owner_id,
            name=name,
            title=title,
            stickers=stickers,
            sticker_type=pack_type,
            needs_repainting=False,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=20,
            pool_timeout=20,
        )
        link_prefix = "addemoji" if pack_type == "custom_emoji" else "addstickers"
        return PublishedPack(
            name=name,
            title=title,
            pack_type=pack_type,
            link=f"https://t.me/{link_prefix}/{name}",
        )
