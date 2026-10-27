import asyncio

import pytest

from app.telegram.sticker_api import TelegramStickerPublisher


class FakeBot:
    def __init__(self):
        self.calls = []

    async def create_new_sticker_set(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.parametrize(
    ("pack_type", "prefix"),
    [("custom_emoji", "addemoji"), ("regular", "addstickers")],
)
def test_publisher_maps_type_and_deep_link(pack_type, prefix):
    bot = FakeBot()

    async def publish():
        return await TelegramStickerPublisher().create_pack(
            bot=bot,
            owner_id=42,
            bot_username="example_bot",
            text="Hello World",
            assets=[b"tgs-bytes"],
            pack_type=pack_type,
        )

    published = asyncio.run(publish())
    assert len(bot.calls) == 1
    assert bot.calls[0]["sticker_type"] == pack_type
    assert bot.calls[0]["user_id"] == 42
    assert bot.calls[0]["stickers"][0].format == "animated"
    assert published.link.startswith(f"https://t.me/{prefix}/")
    assert published.name.endswith("_by_example_bot")


def test_publisher_rejects_unknown_pack_type_before_api_call():
    bot = FakeBot()

    async def publish_invalid():
        with pytest.raises(ValueError):
            await TelegramStickerPublisher().create_pack(
                bot=bot,
                owner_id=42,
                bot_username="example_bot",
                text="Hello",
                assets=[b"tgs-bytes"],
                pack_type="unknown",
            )

    asyncio.run(publish_invalid())
    assert not bot.calls
