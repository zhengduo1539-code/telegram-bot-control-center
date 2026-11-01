"""Regression coverage for preserving Telegram message entities end-to-end."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import MessageEntity

import database
import scheduler


ENTITY = {"type": "bold", "offset": 0, "length": 5}


def test_build_scheduled_post_persists_telegram_entities():
    doc = database.build_scheduled_post(
        user_id=7,
        chat_id=-100123,
        content_text="Hello world",
        content_entities=[ENTITY],
    )
    assert doc["content"]["entities"] == [ENTITY]


def test_send_post_restores_entities_for_text_message():
    async def scenario():
        bot = SimpleNamespace(send_message=AsyncMock(return_value=object()))
        post = {
            "chat_id": -100123,
            "content": {"text": "Hello world", "entities": [ENTITY]},
        }

        await scheduler._send_post(bot, post)

        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["entities"] == [MessageEntity(type="bold", offset=0, length=5)]

    asyncio.run(scenario())


def test_send_post_restores_entities_for_media_caption():
    async def scenario():
        bot = SimpleNamespace(send_photo=AsyncMock(return_value=object()))
        post = {
            "chat_id": -100123,
            "content": {
                "text": "Hello world",
                "media_file_id": "telegram-file-id",
                "media_type": "photo",
                "entities": [ENTITY],
            },
        }

        await scheduler._send_post(bot, post)

        kwargs = bot.send_photo.await_args.kwargs
        assert kwargs["caption_entities"] == [MessageEntity(type="bold", offset=0, length=5)]

    asyncio.run(scenario())


def test_add_to_media_pool_assigns_stable_item_id(monkeypatch):
    async def scenario():
        class Collection:
            async def update_one(self, query, update, **kwargs):
                self.query = query
                self.update = update

        collection = Collection()

        class DB:
            def __getitem__(self, name):
                assert name == database.COL_MEDIA_POOLS
                return collection

        monkeypatch.setattr(database, "get_db", AsyncMock(return_value=DB()))
        item = {"text": "Same text", "entities": [ENTITY]}

        await database.add_to_media_pool(7, -100123, item)

        stored = collection.update["$push"]["items"]
        assert stored["item_id"]
        assert stored["posted"] is False
        assert stored["entities"] == [ENTITY]

    asyncio.run(scenario())


def test_mark_pool_item_posted_prefers_stable_item_id(monkeypatch):
    async def scenario():
        class Collection:
            async def update_one(self, query, update):
                self.query = query
                self.update = update

        collection = Collection()

        class DB:
            def __getitem__(self, name):
                assert name == database.COL_MEDIA_POOLS
                return collection

        monkeypatch.setattr(database, "get_db", AsyncMock(return_value=DB()))

        await database.mark_pool_item_posted(
            7, -100123, "duplicate-file-id", "duplicate text", "unique-item-id"
        )

        assert collection.query == {
            "user_id": 7,
            "chat_id": -100123,
            "items.item_id": "unique-item-id",
        }
        assert collection.update["$set"]["items.$.posted"] is True

    asyncio.run(scenario())
