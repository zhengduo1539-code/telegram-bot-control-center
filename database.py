"""MongoDB-backed daily quota store with atomic increments and TTL cleanup."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

DAILY_LIMIT = 3
RETENTION_DAYS = 60


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
        self.usage = self.db["daily_usage"]

    def initialize(self) -> None:
        self.client.admin.command("ping")
        self.usage.create_index([("user_id", ASCENDING), ("day", ASCENDING)], unique=True, name="user_day_unique")
        self.usage.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="usage_ttl")

    def ping(self) -> None:
        self.client.admin.command("ping")

    @staticmethod
    def current_day(now: datetime | None = None) -> str:
        current = now or datetime.now(timezone.utc)
        return current.astimezone(timezone.utc).date().isoformat()

    @staticmethod
    def _expiry(day_value: str) -> datetime:
        usage_day = date.fromisoformat(day_value)
        expires = datetime.combine(usage_day + timedelta(days=RETENTION_DAYS), time.min)
        return expires.replace(tzinfo=timezone.utc)

    def reserve(self, user_id: int, now: datetime | None = None) -> int | None:
        """Atomically reserve one daily slot. Returns new count or None when full."""
        day_value = self.current_day(now)
        query = {"user_id": int(user_id), "day": day_value, "count": {"$lt": DAILY_LIMIT}}
        update = {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "user_id": int(user_id),
                "day": day_value,
                "expires_at": self._expiry(day_value),
                "created_at": datetime.now(timezone.utc),
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
        try:
            document = self.usage.find_one_and_update(
                query, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError:
            existing = self.usage.find_one({"user_id": int(user_id), "day": day_value})
            if not existing or int(existing.get("count", 0)) >= DAILY_LIMIT:
                return None
            document = self.usage.find_one_and_update(
                query, update, upsert=False, return_document=ReturnDocument.AFTER
            )
        if document and 1 <= int(document.get("count", 0)) <= DAILY_LIMIT:
            return int(document["count"])
        return None

    def release(self, user_id: int, now: datetime | None = None) -> None:
        """Return a reserved slot if rendering or Telegram upload fails."""
        self.usage.update_one(
            {"user_id": int(user_id), "day": self.current_day(now), "count": {"$gt": 0}},
            {"$inc": {"count": -1}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        )

    def get_count(self, user_id: int, now: datetime | None = None) -> int:
        doc = self.usage.find_one({"user_id": int(user_id), "day": self.current_day(now)}, {"count": 1})
        return int(doc.get("count", 0)) if doc else 0

    def today_stats(self, now: datetime | None = None) -> dict[str, int]:
        day_value = self.current_day(now)
        stats = list(self.usage.aggregate([
            {"$match": {"day": day_value}},
            {"$group": {"_id": None, "users": {"$sum": 1}, "packs": {"$sum": "$count"}}},
        ]))
        if not stats:
            return {"users_today": 0, "packs_today": 0}
        return {"users_today": int(stats[0]["users"]), "packs_today": int(stats[0]["packs"])}

    def close(self) -> None:
        self.client.close()
