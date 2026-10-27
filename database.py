"""Compact MongoDB daily-quota store with safe legacy-schema migration."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

logger = logging.getLogger(__name__)

DAILY_LIMIT = 3
MAX_DAILY_USAGE = 10000
RETENTION_DAYS = 60
COLLECTION_NAME = "daily_usage"
USER_COLLECTION_NAME = "user_state"
PAYMENT_COLLECTION_NAME = "star_payments"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Previous schema -> compact schema. Keep this mapping in one place so
# migrations, queries, indexes, and tests use the same contract.
LEGACY_FIELDS = ("user_id", "day", "count", "expires_at", "created_at", "updated_at")
LEGACY_INDEX_FIELDS = {"user_id", "day", "expires_at"}
STYLE_STORAGE_FIELDS = {"font": "f", "color": "c", "shape": "h", "animation": "a"}


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
    def __init__(
        self,
        mongo_uri: str,
        database_name: str | None = None,
        auth_source: str | None = None,
    ) -> None:
        client_options: dict[str, Any] = {
            "appname": "TelegramAnimatedTextCreator",
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 8000,
            "retryWrites": True,
        }
        # Some deployments use a non-default authentication database. Keep
        # the URI untouched unless an explicit override is configured.
        if auth_source and auth_source.strip():
            client_options["authSource"] = auth_source.strip()

        self.client = MongoClient(mongo_uri, **client_options)
        configured_database = (database_name or "").strip()
        if configured_database:
            self.db = self.client[configured_database]
        else:
            # Preserve a database selected in the connection URI; use a
            # dedicated app database only when the URI has no database path.
            self.db = self.client.get_default_database(default="animated_text_creator")
        self.usage = self.db[COLLECTION_NAME]
        self.extra_usage = self.db["extra_usage"]
        self.users = self.db[USER_COLLECTION_NAME]
        self.payments = self.db[PAYMENT_COLLECTION_NAME]

    def initialize(self) -> None:
        """Verify MongoDB, migrate old documents, then enforce compact indexes."""
        logger.info("Initializing quota storage in MongoDB database '%s'", self.db.name)
        self.client.admin.command("ping")
        collection_created_with_validator = self._ensure_collection()
        self._migrate_legacy_schema()
        self.usage.delete_many({"n": {"$lte": 0}})
        self._ensure_indexes()
        if not collection_created_with_validator:
            self._ensure_schema_validator()
        self._ensure_account_indexes()
        self._ensure_extra_usage_indexes()

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

    def _ensure_account_indexes(self) -> None:
        self.users.create_index([("u", ASCENDING)], unique=True, name="user_uq")
        self.payments.create_index([("c", ASCENDING)], unique=True, name="charge_uq")
        self.payments.create_index([("u", ASCENDING), ("d", ASCENDING)], name="user_payment_history")

    def _ensure_extra_usage_indexes(self) -> None:
        """Store PRO/referral overage separately so old quota validators stay compatible."""
        self.extra_usage.create_index(
            [("u", ASCENDING), ("d", ASCENDING)], unique=True, name="extra_ud_uq"
        )
        self.extra_usage.create_index(
            [("x", ASCENDING)], expireAfterSeconds=0, name="extra_x_ttl"
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

    def reserve(
        self,
        user_id: int,
        now: datetime | None = None,
        *,
        allow_over_limit: bool = False,
    ) -> int | None:
        """Atomically reserve a daily slot; ordinary users stop at 3 unless entitled."""
        user_key = self._user_key(user_id)
        day_value = self.current_day(now)
        # Keep the historic daily_usage collection limited to the original
        # free quota. PRO and referral overage live in their own collection so
        # older MongoDB schema validators never block paid/bonus generations.
        usage = self.extra_usage if allow_over_limit else self.usage
        maximum = MAX_DAILY_USAGE if allow_over_limit else DAILY_LIMIT
        query = {"u": user_key, "d": day_value, "n": {"$lt": maximum}}
        update = {
            "$inc": {"n": 1},
            "$setOnInsert": {
                "u": user_key,
                "d": day_value,
                "x": self._expiry(day_value),
            },
        }
        try:
            document = usage.find_one_and_update(
                query, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError:
            # Concurrent first-time requests can race on the unique (u, d)
            # index. Read the winner and retry only while quota remains.
            existing = usage.find_one({"u": user_key, "d": day_value}, {"n": 1})
            if not existing or int(existing.get("n", 0)) >= maximum:
                return None
            document = usage.find_one_and_update(
                query, update, upsert=False, return_document=ReturnDocument.AFTER
            )
        if document and 1 <= int(document.get("n", 0)) <= maximum:
            return int(document["n"])
        return None

    def release(
        self,
        user_id: int,
        now: datetime | None = None,
        *,
        allow_over_limit: bool = False,
    ) -> None:
        """Return a reserved slot after failure, using its original quota bucket."""
        user_key = self._user_key(user_id)
        day_value = self.current_day(now)
        usage = self.extra_usage if allow_over_limit else self.usage
        usage.update_one(
            {"u": user_key, "d": day_value, "n": {"$gt": 0}},
            {"$inc": {"n": -1}},
        )
        usage.delete_one({"u": user_key, "d": day_value, "n": 0})

    def get_count(self, user_id: int, now: datetime | None = None) -> int:
        user_key = self._user_key(user_id)
        day_value = self.current_day(now)
        free_doc = self.usage.find_one({"u": user_key, "d": day_value}, {"n": 1})
        extra_doc = self.extra_usage.find_one({"u": user_key, "d": day_value}, {"n": 1})
        return (
            int(free_doc.get("n", 0)) if free_doc else 0
        ) + (
            int(extra_doc.get("n", 0)) if extra_doc else 0
        )

    def today_stats(self, now: datetime | None = None) -> dict[str, int]:
        day_value = self.current_day(now)
        pack_total = 0
        active_users: set[int] = set()
        for collection in (self.usage, self.extra_usage):
            stats = list(
                collection.aggregate(
                    [
                        {"$match": {"d": day_value, "n": {"$gt": 0}}},
                        {
                            "$group": {
                                "_id": None,
                                "packs": {"$sum": "$n"},
                            }
                        },
                    ]
                )
            )
            if stats:
                pack_total += int(stats[0].get("packs", 0))
            active_users.update(
                int(value) for value in collection.distinct("u", {"d": day_value, "n": {"$gt": 0}})
            )
        return {"users_today": len(active_users), "packs_today": pack_total}

    def register_user(self, user_id: int, referrer_id: int | None = None) -> bool:
        """Register a user once and award one referral credit for a genuine new invite."""
        user_key = self._user_key(user_id)
        referrer = self._user_key(referrer_id) if referrer_id is not None else None
        if referrer == user_key:
            referrer = None
        if referrer is not None:
            known_referrer = (
                self.users.find_one({"u": referrer}, {"u": 1}) is not None
                or self.usage.find_one({"u": referrer}, {"u": 1}) is not None
            )
            if not known_referrer:
                referrer = None

        if self.users.find_one({"u": user_key}, {"u": 1}):
            return False

        had_usage = self.usage.find_one({"u": user_key}, {"u": 1}) is not None
        initial: dict[str, Any] = {"u": user_key, "b": 0, "p": EPOCH}
        result = self.users.update_one({"u": user_key}, {"$setOnInsert": initial}, upsert=True)
        if result.upserted_id is None or had_usage or referrer is None:
            return False

        self.users.update_one({"u": user_key}, {"$set": {"r": referrer}})
        self.users.update_one(
            {"u": referrer},
            {"$inc": {"b": 1}, "$setOnInsert": {"u": referrer, "p": EPOCH}},
            upsert=True,
        )
        return True

    def get_account(self, user_id: int, now: datetime | None = None) -> dict[str, Any]:
        """Return stable account entitlements without exposing raw database documents."""
        user_key = self._user_key(user_id)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        document = self.users.find_one({"u": user_key}) or {}
        pro_until = document.get("p", EPOCH)
        if not isinstance(pro_until, datetime):
            pro_until = EPOCH
        if pro_until.tzinfo is None:
            pro_until = pro_until.replace(tzinfo=timezone.utc)
        pro_until = pro_until.astimezone(timezone.utc)
        return {
            "user_id": user_key,
            "bonus_count": max(0, int(document.get("b", 0))),
            "pro_until": pro_until,
            "is_pro": pro_until > current,
        }

    def get_style(self, user_id: int) -> dict[str, str] | None:
        """Return the user's saved appearance; unknown or partial values are ignored."""
        user_key = self._user_key(user_id)
        document = self.users.find_one({"u": user_key}, {"s": 1}) or {}
        stored = document.get("s")
        if not isinstance(stored, dict):
            return None

        style: dict[str, str] = {}
        for public_field, stored_field in STYLE_STORAGE_FIELDS.items():
            value = stored.get(stored_field)
            if not isinstance(value, str) or not value or len(value) > 32:
                return None
            style[public_field] = value
        return style

    def save_style(self, user_id: int, style: dict[str, str]) -> None:
        """Persist only the four small style choices in the existing user_state row."""
        user_key = self._user_key(user_id)
        if not isinstance(style, dict) or set(style) != set(STYLE_STORAGE_FIELDS):
            raise ValueError("style must contain font, color, shape, and animation")

        stored: dict[str, str] = {}
        for public_field, stored_field in STYLE_STORAGE_FIELDS.items():
            value = style[public_field]
            if not isinstance(value, str) or not value.strip() or len(value) > 32:
                raise ValueError(f"invalid style field: {public_field}")
            stored[stored_field] = value.strip()

        self.users.update_one(
            {"u": user_key},
            {
                "$set": {"s": stored},
                "$setOnInsert": {"u": user_key, "b": 0, "p": EPOCH},
            },
            upsert=True,
        )
    def consume_bonus(self, user_id: int) -> bool:
        """Atomically consume one referral pack credit, if available."""
        user_key = self._user_key(user_id)
        updated = self.users.find_one_and_update(
            {"u": user_key, "b": {"$gt": 0}},
            {"$inc": {"b": -1}},
            return_document=ReturnDocument.AFTER,
        )
        return updated is not None

    def add_bonus(self, user_id: int, amount: int = 1) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1 or amount > 100:
            raise ValueError("bonus amount must be between 1 and 100")
        user_key = self._user_key(user_id)
        self.users.update_one(
            {"u": user_key},
            {"$inc": {"b": amount}, "$setOnInsert": {"u": user_key, "p": EPOCH}},
            upsert=True,
        )

    def record_pro_payment(
        self,
        *,
        user_id: int,
        charge_id: str,
        amount: int,
        payload: str,
        expires_at: datetime,
        paid_at: datetime | None = None,
    ) -> bool:
        """Record a Stars charge exactly once and activate PRO only for a new charge."""
        user_key = self._user_key(user_id)
        if not charge_id or len(charge_id) > 256:
            raise ValueError("invalid Telegram payment charge ID")
        if not isinstance(expires_at, datetime):
            raise ValueError("expires_at must be a datetime")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at = expires_at.astimezone(timezone.utc)
        when = paid_at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone(timezone.utc)
        is_new_charge = True
        try:
            self.payments.insert_one({
                "c": charge_id, "u": user_key, "a": int(amount),
                "p": str(payload)[:128], "d": when, "x": expires_at,
            })
        except DuplicateKeyError:
            is_new_charge = False
        # Always reconcile the entitlement after a charge was seen. If a prior
        # write stored the charge but failed before updating user_state, webhook
        # redelivery repairs the entitlement instead of leaving PRO inactive.
        self.users.update_one(
            {"u": user_key},
            {"$max": {"p": expires_at}, "$setOnInsert": {"u": user_key, "b": 0}},
            upsert=True,
        )
        return is_new_charge

    def close(self) -> None:
        self.client.close()


def _is_authorization_error(exc: OperationFailure) -> bool:
    message = str(exc).lower()
    return exc.code in {13, 18, 8000} or "not authorized" in message or "unauthorized" in message
