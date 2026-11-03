from datetime import datetime, timezone

import pytest

import database


class _Result:
    modified_count = 2


class _Collection:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))

    async def update_many(self, query, update):
        self.calls.append((query, update))
        return _Result()


class _DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["posted", "failed"])
async def test_terminal_post_status_sets_expiry(monkeypatch, status):
    fake_db = _DB()

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(database, "get_db", fake_get_db)
    await database.update_post_status("507f1f77bcf86cd799439011", status)

    update = fake_db[database.COL_POSTS].calls[0][1]
    expire_at = update["$set"]["expire_at"]
    assert isinstance(expire_at, datetime)
    assert expire_at.tzinfo == timezone.utc
    assert expire_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "paused"])
async def test_active_post_status_removes_expiry(monkeypatch, status):
    fake_db = _DB()

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(database, "get_db", fake_get_db)
    await database.update_post_status("507f1f77bcf86cd799439011", status)

    update = fake_db[database.COL_POSTS].calls[0][1]
    assert update["$unset"] == {"expire_at": ""}



@pytest.mark.asyncio
async def test_legacy_media_pool_schedules_are_paused_without_deletion(monkeypatch):
    fake_db = _DB()

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(database, "get_db", fake_get_db)
    paused = await database.pause_legacy_pool_posts()

    query, update = fake_db[database.COL_POSTS].calls[0]
    assert query == {"recurrence.type": "pool", "status": "pending"}
    assert update["$set"]["status"] == "paused"
    assert update["$set"]["legacy_pause_reason"]
    assert update["$unset"] == {"expire_at": ""}
    assert paused == 2


def test_scheduled_post_builder_rejects_retired_pool_recurrence():
    with pytest.raises(ValueError):
        database.build_scheduled_post(
            user_id=123,
            chat_id=-1001234567890,
            recurrence_type="pool",
        )
