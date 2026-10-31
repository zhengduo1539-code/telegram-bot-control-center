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
async def test_queue_cleanup_only_targets_old_terminal_entries(monkeypatch):
    fake_db = _DB()

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(database, "get_db", fake_get_db)
    removed = await database.cleanup_expired_queue_history(days=30)

    query, update = fake_db[database.COL_QUEUE_SLOTS].calls[0]
    assert query == {}
    clauses = update["$pull"]["contents"]["$or"]
    assert {clause["state"] for clause in clauses} == {"posted", "failed"}
    assert removed == 2


@pytest.mark.asyncio
async def test_queue_cleanup_rejects_non_positive_retention(monkeypatch):
    async def fail_get_db():
        raise AssertionError("database should not be touched")

    monkeypatch.setattr(database, "get_db", fail_get_db)
    with pytest.raises(ValueError):
        await database.cleanup_expired_queue_history(days=0)
