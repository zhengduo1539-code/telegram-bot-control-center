"""Pure timezone-aware helpers shared by scheduler code and unit tests."""
from __future__ import annotations
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
import pytz

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"

_DATETIME_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})$")


def parse_schedule_datetime(
    value: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Parse a future local time and return an aware UTC datetime.

    Accepts ``HH:MM`` (the next valid occurrence) or
    ``DD/MM/YYYY HH:MM`` (that exact future local date/time). Ambiguous
    daylight-saving times consistently use standard time. Nonexistent local
    times are rejected for explicit dates and skipped for daily HH:MM input.
    """
    if not isinstance(value, str) or not isinstance(timezone_name, str):
        return None
    raw = value.strip()
    if not raw:
        return None

    try:
        zone = pytz.timezone(timezone_name)
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        return None

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("The reference datetime must be timezone-aware.")
    reference_utc = reference.astimezone(timezone.utc)

    date_match = _DATETIME_RE.fullmatch(raw)
    if date_match:
        day, month, year, hour, minute = map(int, date_match.groups())
        try:
            naive = datetime(year, month, day, hour, minute)
        except ValueError:
            return None
        try:
            try:
                local_candidate = zone.localize(naive, is_dst=None)
            except pytz.AmbiguousTimeError:
                local_candidate = zone.localize(naive, is_dst=False)
            except pytz.NonExistentTimeError:
                return None
        except (ValueError, OverflowError):
            return None
        candidate_utc = local_candidate.astimezone(timezone.utc)
        return candidate_utc if candidate_utc > reference_utc else None

    normalized = parse_hhmm(raw)
    if normalized is None:
        return None
    hour, minute = map(int, normalized.split(":"))
    local_reference = reference_utc.astimezone(zone)

    # A wall-clock time can be skipped by a daylight-saving transition.
    # Search subsequent dates and skip the gap instead of shifting the hour.
    for day_offset in range(3):
        candidate_date = local_reference.date() + timedelta(days=day_offset)
        naive = datetime.combine(candidate_date, time(hour, minute))
        try:
            try:
                local_candidate = zone.localize(naive, is_dst=None)
            except pytz.AmbiguousTimeError:
                local_candidate = zone.localize(naive, is_dst=False)
            except pytz.NonExistentTimeError:
                continue
        except (ValueError, OverflowError):
            continue
        candidate_utc = local_candidate.astimezone(timezone.utc)
        if candidate_utc > reference_utc:
            return candidate_utc
    return None


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


def next_weekday_slot(
    days_of_week: Iterable[int],
    timezone_name: str,
    slot: str,
    after: datetime,
) -> datetime | None:
    """Return the next selected weekday occurrence as an aware UTC datetime.

    Local wall-clock times remain stable through DST changes. Ambiguous times
    use standard time, while nonexistent times are skipped to the next
    selected weekday rather than being silently shifted by an hour.
    """
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("The reference datetime must be timezone-aware.")
    normalized = parse_hhmm(slot)
    if normalized is None:
        raise ValueError("The weekday slot must be a valid HH:MM value.")
    selected = set(days_of_week)
    if not selected or any(not isinstance(day, int) or day not in range(7) for day in selected):
        raise ValueError("days_of_week must contain weekdays numbered 0 through 6.")
    try:
        zone = pytz.timezone(timezone_name)
    except pytz.exceptions.UnknownTimeZoneError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc

    hour, minute = map(int, normalized.split(":"))
    after_utc = after.astimezone(timezone.utc)
    local_after = after_utc.astimezone(zone)
    for day_offset in range(1, 9):
        candidate_date = local_after.date() + timedelta(days=day_offset)
        if candidate_date.weekday() not in selected:
            continue
        naive = datetime.combine(candidate_date, time(hour, minute))
        try:
            try:
                local_candidate = zone.localize(naive, is_dst=None)
            except pytz.AmbiguousTimeError:
                local_candidate = zone.localize(naive, is_dst=False)
            except pytz.NonExistentTimeError:
                continue
        except (ValueError, OverflowError):
            continue
        candidate_utc = local_candidate.astimezone(timezone.utc)
        if candidate_utc > after_utc:
            return candidate_utc
    return None


def is_future_datetime(value: datetime, now: datetime | None = None) -> bool:
    if value.tzinfo is None or value.utcoffset() is None:
        return False
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("The reference datetime must be timezone-aware.")
    return value.astimezone(timezone.utc) > reference.astimezone(timezone.utc)
