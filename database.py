"""Compact MongoDB daily-quota store with safe legacy-schema migration."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

logger = logging.getLogger(__name__)

DAILY_LIMIT = 3
RETENTION_DAYS = 60
COLLECTION_NAME = "daily_usage"

# Previous schema -> compact schema. Keep this mapping in one place so
# migrations, queries, indexes, and tests use the same contract.
LEGACY_FIELDS = ("user_id", "day", "count", "expires_at", "created_at", "updated_at")
LEGACY_INDEX_FIELDS = {"user_id", "day", "expires_at"}


def _schema_validator() -> dict[str, Any]:
    """MongoDB JSON Schema validator for the compact quota document."""
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["u", "d", "n", "x"],
            "additionalProperties": False,
            "properties": {
                "_id": {"bsonType": "objectId"},
                "u": {"bsonType": ["int", "long"], "minimum": 1},
                "d": {
                    "bsonType": "string",
                    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
                },
                "n": {
                    "bsonType": ["int", "long"],
                    "minimum": 0,
                    "maximum": DAILY_LIMIT,
                },
                "x": {"bsonType": "date"},
            },
        }
    }


class UsageStore:
    def __init__(self, mongo_uri: str) -> None:
        self.client = MongoClient(
            mongo_uri,
            appname="TelegramAnimatedTextCreator",
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=8000,
            retryWrites=True,
        )
        self.db = self.client.get_default_database(default="animated_text_creator")
        self.usage = self.db[COLLECTION_NAME]

    def initialize(self) -> None:
        """Verify MongoDB, migrate old documents, then enforce compact indexes."""
        self.client.admin.command("ping")
        collection_created_with_validator = self._ensure_collection()
        self._migrate_legacy_schema()
        self.usage.delete_many({"n": {"$lte": 0}})
        self._ensure_indexes()
        if not collection_created_with_validator:
            self._ensure_schema_validator()

    def _ensure_collection(self) -> bool:
        """Create a fresh collection with validation where supported.

        Returns True when this call created the collection with its validator.
        Existing collections are migrated before the validator is applied.
        """
        existing = self.db.list_collection_names(filter={"name": self.usage.name})
        if existing:
            return False

        try:
            self.db.create_collection(
                self.usage.name,
                validator=_schema_validator(),
                validationLevel="strict",
                validationAction="error",
            )
            logger.info("Created MongoDB collection %s with schema validation", self.usage.name)
            return True
        except OperationFailure as exc:
            # Another instance could have created the collection between the
            # list and create calls. Continue only for that specific race.
            if exc.code == 48:
                return False
            if _is_authorization_error(exc):
                # Least-privilege application accounts may have createCollection
                # but not permission to specify validators. Keep the application
                # operational; unique indexes and application invariants remain.
                logger.warning(
                    "MongoDB user cannot create a schema validator; "
                    "application-level validation and indexes will still be used."
                )
                self.db.create_collection(self.usage.name)
                return False
            raise

    def _migrate_legacy_schema(self) -> None:
        """Rename legacy fields in-place without losing quota counts or expiry.

        Old indexes must be removed before field migration; the compact indexes
        are created immediately afterward. The operation is idempotent.
        """
        for index in list(self.usage.list_indexes()):
            index_name = index.get("name")
            index_fields = set(index.get("key", {}).keys())
            if index_name != "_id_" and index_fields.intersection(LEGACY_INDEX_FIELDS):
                self.usage.drop_index(index_name)
                logger.info("Dropped legacy MongoDB index %s", index_name)

        legacy_filter = {
            "$or": [{field: {"$exists": True}} for field in LEGACY_FIELDS]
        }
        pipeline = [
            {
                "$set": {
                    "u": {"$ifNull": ["$u", "$user_id"]},
                    "d": {"$ifNull": ["$d", "$day"]},
                    "n": {"$ifNull": ["$n", "$count"]},
                    "x": {"$ifNull": ["$x", "$expires_at"]},
                }
            },
            {"$unset": list(LEGACY_FIELDS)},
        ]
        result = self.usage.update_many(legacy_filter, pipeline)
        if result.modified_count:
            logger.info(
                "Migrated %s MongoDB usage document(s) to compact schema",
                result.modified_count,
            )

    def _ensure_indexes(self) -> None:
        self.usage.create_index(
            [("u", ASCENDING), ("d", ASCENDING)],
            unique=True,
            name="ud_uq",
        )
        self.usage.create_index(
            [("x", ASCENDING)],
            expireAfterSeconds=0,
            name="x_ttl",
        )

    def _ensure_schema_validator(self) -> None:
        """Apply strict document validation if the DB user has collMod rights."""
        try:
            self.db.command(
                {
                    "collMod": self.usage.name,
                    "validator": _schema_validator(),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        except OperationFailure as exc:
            if _is_authorization_error(exc):
                # Do not require broad dbAdmin privileges for a bot whose data
                # access otherwise needs only normal collection permissions.
                logger.warning(
                    "MongoDB account lacks collMod permission; compact-field "
                    "validation remains enforced by the application and indexes."
                )
                return
            raise

    def ping(self) -> None:
        self.client.admin.command("ping")

    @staticmethod
    def _user_key(user_id: int) -> int:
        if isinstance(user_id, bool):
            raise ValueError("user_id must be a positive integer")
        try:
            value = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("user_id must be a positive integer") from exc
        if value <= 0:
            raise ValueError("user_id must be a positive integer")
        return value

    @staticmethod
    def current_day(now: datetime | None = None) -> str:
        current = now or datetime.now(timezone.utc)
        # Treat explicitly naive test/caller timestamps as UTC so results never
        # depend on the host's local timezone.
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).date().isoformat()

    @staticmethod
    def _expiry(day_value: str) -> datetime:
        usage_day = date.fromisoformat(day_value)
        expires = datetime.combine(usage_day + timedelta(days=RETENTION_DAYS), time.min)
        return expires.replace(tzinfo=timezone.utc)

    def reserve(self, user_id: int, now: datetime | None = None) -> int | None:
        """Atomically reserve one daily slot; return its new count or None if full."""
        user_key = self._user_key(user_id)
        day_value = self.current_day(now)
        query = {"u": user_key, "d": day_value, "n": {"$lt": DAILY_LIMIT}}
        update = {
            "$inc": {"n": 1},
            "$setOnInsert": {
                "u": user_key,
                "d": day_value,
                "x": self._expiry(day_value),
            },
        }
        try:
            document = self.usage.find_one_and_update(
                query, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError:
            # Concurrent first-time requests can race on the unique (u, d)
            # index. Read the winner and retry only while quota remains.
            existing = self.usage.find_one({"u": user_key, "d": day_value}, {"n": 1})
            if not existing or int(existing.get("n", 0)) >= DAILY_LIMIT:
                return None
            document = self.usage.find_one_and_update(
                query, update, upsert=False, return_document=ReturnDocument.AFTER
            )
        if document and 1 <= int(document.get("n", 0)) <= DAILY_LIMIT:
            return int(document["n"])
        return None

    def release(self, user_id: int, now: datetime | None = None) -> None:
        """Return a reserved slot after failure, using the original reservation day."""
        user_key = self._user_key(user_id)
        day_value = self.current_day(now)
        self.usage.update_one(
            {"u": user_key, "d": day_value, "n": {"$gt": 0}},
            {"$inc": {"n": -1}},
        )
        # Empty quota documents carry no useful state. The predicate prevents
        # deleting a document another concurrent reservation has already reused.
        self.usage.delete_one({"u": user_key, "d": day_value, "n": 0})

    def get_count(self, user_id: int, now: datetime | None = None) -> int:
        user_key = self._user_key(user_id)
        document = self.usage.find_one(
            {"u": user_key, "d": self.current_day(now)}, {"n": 1}
        )
        return int(document.get("n", 0)) if document else 0

    def today_stats(self, now: datetime | None = None) -> dict[str, int]:
        day_value = self.current_day(now)
        stats = list(
            self.usage.aggregate(
                [
                    {"$match": {"d": day_value, "n": {"$gt": 0}}},
                    {
                        "$group": {
                            "_id": None,
                            "users": {"$sum": 1},
                            "packs": {"$sum": "$n"},
                        }
                    },
                ]
            )
        )
        if not stats:
            return {"users_today": 0, "packs_today": 0}
        return {
            "users_today": int(stats[0]["users"]),
            "packs_today": int(stats[0]["packs"]),
        }

    def close(self) -> None:
        self.client.close()


def _is_authorization_error(exc: OperationFailure) -> bool:
    message = str(exc).lower()
    return exc.code in {13, 18, 8000} or "not authorized" in message or "unauthorized" in message
