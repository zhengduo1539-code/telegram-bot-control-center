"""Pure timezone-aware helpers shared by scheduler code and unit tests."""
from __future__ import annotations
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
import pytz

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(value: str) -> str | None:
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def next_daily_slot(slots: Iterable[str], timezone_name: str, after: datetime) -> datetime | None:
    """Return the next configured local daily slot as an aware UTC datetime.

    Ambiguous daylight-saving times choose standard time. Non-existent times
    are skipped for that day so a queue never receives an invalid timestamp.
    """
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("The reference datetime must be timezone-aware.")
    try:
        zone = pytz.timezone(timezone_name)
    except pytz.exceptions.UnknownTimeZoneError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    parsed = [parse_hhmm(slot) for slot in slots]
    if not parsed or any(slot is None for slot in parsed):
        raise ValueError("Queue slots must be valid HH:MM values.")
    normalized = sorted(set(parsed))
    local_after = after.astimezone(zone)
    for day_offset in range(3):
        slot_date: date = local_after.date() + timedelta(days=day_offset)
        for slot in normalized:
            hour, minute = map(int, slot.split(":"))
            naive = datetime.combine(slot_date, time(hour, minute))
            try:
                local_candidate = zone.localize(naive, is_dst=None)
            except pytz.AmbiguousTimeError:
                local_candidate = zone.localize(naive, is_dst=False)
            except pytz.NonExistentTimeError:
                continue
            if local_candidate > local_after:
                return local_candidate.astimezone(timezone.utc)
    return None


def is_future_datetime(value: datetime, now: datetime | None = None) -> bool:
    if value.tzinfo is None or value.utcoffset() is None:
        return False
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("The reference datetime must be timezone-aware.")
    return value.astimezone(timezone.utc) > reference.astimezone(timezone.utc)
