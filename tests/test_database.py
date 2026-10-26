from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import pytest

from database import DAILY_LIMIT, RETENTION_DAYS, UsageStore, _schema_validator


@pytest.fixture
def mongo_store(monkeypatch):
    client = MagicMock()
    db = MagicMock()
    collection = MagicMock()
    client.get_default_database.return_value = db
    db.__getitem__.return_value = collection
    db.list_collection_names.return_value = ["daily_usage"]
    collection.name = "daily_usage"
    collection.list_indexes.return_value = [
        {"name": "_id_", "key": {"_id": 1}},
        {"name": "user_day_unique", "key": {"user_id": 1, "day": 1}},
        {"name": "usage_ttl", "key": {"expires_at": 1}},
    ]
    monkeypatch.setattr("database.MongoClient", MagicMock(return_value=client))
    store = UsageStore("mongodb://example.invalid/emoji")
    return store, client, db, collection


def test_current_day_is_utc():
    sample = datetime(2026, 10, 27, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    assert UsageStore.current_day(sample) == "2026-10-26"


def test_naive_datetime_is_treated_as_utc():
    sample = datetime(2026, 10, 26, 23, 0)
    assert UsageStore.current_day(sample) == "2026-10-26"


def test_expiry_is_midnight_utc_after_retention_window():
    expiry = UsageStore._expiry("2026-10-26")
    assert expiry == datetime(2026, 10, 26, tzinfo=timezone.utc) + timedelta(days=RETENTION_DAYS)


def test_user_ids_must_be_positive_integers(mongo_store):
    store, _, _, _ = mongo_store
    assert store._user_key("12345") == 12345
    for invalid in (0, -1, True, "not-an-id", None):
        with pytest.raises(ValueError):
            store._user_key(invalid)


def test_initialize_migrates_legacy_fields_and_rebuilds_compact_indexes(mongo_store):
    store, client, db, collection = mongo_store

    store.initialize()

    client.admin.command.assert_called_once_with("ping")
    collection.drop_index.assert_has_calls(
        [call("user_day_unique"), call("usage_ttl")], any_order=True
    )

    legacy_filter, pipeline = collection.update_many.call_args.args
    assert legacy_filter == {
        "$or": [
            {"user_id": {"$exists": True}},
            {"day": {"$exists": True}},
            {"count": {"$exists": True}},
            {"expires_at": {"$exists": True}},
            {"created_at": {"$exists": True}},
            {"updated_at": {"$exists": True}},
        ]
    }
    assert pipeline[0]["$set"] == {
        "u": {"$ifNull": ["$u", "$user_id"]},
        "d": {"$ifNull": ["$d", "$day"]},
        "n": {"$ifNull": ["$n", "$count"]},
        "x": {"$ifNull": ["$x", "$expires_at"]},
    }
    assert pipeline[1] == {
        "$unset": ["user_id", "day", "count", "expires_at", "created_at", "updated_at"]
    }

    collection.create_index.assert_has_calls(
        [
            call([("u", 1), ("d", 1)], unique=True, name="ud_uq"),
            call([("x", 1)], expireAfterSeconds=0, name="x_ttl"),
        ],
        any_order=True,
    )
    db.command.assert_called_once()
    validator_command = db.command.call_args.args[0]
    assert validator_command["collMod"] == "daily_usage"
    assert validator_command["validationAction"] == "error"


def test_new_collection_is_created_with_validator(monkeypatch):
    client = MagicMock()
    db = MagicMock()
    collection = MagicMock()
    client.get_default_database.return_value = db
    db.__getitem__.return_value = collection
    db.list_collection_names.return_value = []
    collection.name = "daily_usage"
    collection.list_indexes.return_value = [{"name": "_id_", "key": {"_id": 1}}]
    monkeypatch.setattr("database.MongoClient", MagicMock(return_value=client))

    store = UsageStore("mongodb://example.invalid/emoji")
    store.initialize()

    args, kwargs = db.create_collection.call_args
    assert args == ("daily_usage",)
    assert kwargs["validator"] == _schema_validator()
    assert kwargs["validationLevel"] == "strict"
    assert kwargs["validationAction"] == "error"
    db.command.assert_not_called()


def test_schema_validator_requires_compact_fields_and_forbids_extra_fields():
    validator = _schema_validator()["$jsonSchema"]
    assert validator["required"] == ["u", "d", "n", "x"]
    assert validator["additionalProperties"] is False
    assert validator["properties"]["n"]["maximum"] == DAILY_LIMIT
    assert validator["properties"]["x"]["bsonType"] == "date"


def test_reserve_uses_compact_fields_and_atomic_increment(mongo_store):
    store, _, _, collection = mongo_store
    now = datetime(2026, 10, 26, 23, 30, tzinfo=timezone.utc)
    collection.find_one_and_update.return_value = {"u": 12345, "d": "2026-10-26", "n": 1}

    assert store.reserve(12345, now) == 1

    query, update = collection.find_one_and_update.call_args.args[:2]
    assert query == {"u": 12345, "d": "2026-10-26", "n": {"$lt": DAILY_LIMIT}}
    assert update["$inc"] == {"n": 1}
    assert update["$setOnInsert"] == {
        "u": 12345,
        "d": "2026-10-26",
        "x": UsageStore._expiry("2026-10-26"),
    }
    assert "user_id" not in str(update)
    assert "created_at" not in update["$setOnInsert"]


def test_release_rolls_back_the_original_utc_day_and_removes_empty_document(mongo_store):
    store, _, _, collection = mongo_store
    original_reservation_time = datetime(2026, 10, 26, 23, 59, tzinfo=timezone.utc)

    store.release(12345, original_reservation_time)

    collection.update_one.assert_called_once_with(
        {"u": 12345, "d": "2026-10-26", "n": {"$gt": 0}},
        {"$inc": {"n": -1}},
    )
    collection.delete_one.assert_called_once_with(
        {"u": 12345, "d": "2026-10-26", "n": 0}
    )


def test_get_count_uses_compact_keys(mongo_store):
    store, _, _, collection = mongo_store
    collection.find_one.return_value = {"n": 2}
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)

    assert store.get_count(12345, now) == 2
    collection.find_one.assert_called_once_with(
        {"u": 12345, "d": "2026-10-26"}, {"n": 1}
    )


def test_today_stats_ignores_zero_count_rows(mongo_store):
    store, _, _, collection = mongo_store
    collection.aggregate.return_value = [{"users": 2, "packs": 4}]
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)

    assert store.today_stats(now) == {"users_today": 2, "packs_today": 4}
    collection.aggregate.assert_called_once_with(
        [
            {"$match": {"d": "2026-10-26", "n": {"$gt": 0}}},
            {
                "$group": {
                    "_id": None,
                    "users": {"$sum": 1},
                    "packs": {"$sum": "$n"},
                }
            },
        ]
    )


def test_today_stats_returns_zero_without_records(mongo_store):
    store, _, _, collection = mongo_store
    collection.aggregate.return_value = []

    assert store.today_stats() == {"users_today": 0, "packs_today": 0}
