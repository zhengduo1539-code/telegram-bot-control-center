from datetime import datetime, timezone
import pytest
from schedule_utils import (
    is_future_datetime,
    next_daily_slot,
    parse_hhmm,
    parse_schedule_datetime,
)


def test_parse_hhmm_normalizes_and_validates():
    assert parse_hhmm("9:05") == "09:05"
    assert parse_hhmm("23:59") == "23:59"
    assert parse_hhmm("24:00") is None
    assert parse_hhmm("09:60") is None
    assert parse_hhmm("9") is None


def test_next_daily_slot_uses_selected_timezone():
    after = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
    assert next_daily_slot(["09:00", "14:00"], "Asia/Bangkok", after) == datetime(
        2026, 10, 31, 2, 0, tzinfo=timezone.utc
    )


def test_next_daily_slot_rolls_forward_after_last_slot():
    after = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    assert next_daily_slot(["09:00", "14:00"], "Asia/Bangkok", after) == datetime(
        2026, 11, 1, 2, 0, tzinfo=timezone.utc
    )


def test_next_daily_slot_rejects_naive_datetime():
    with pytest.raises(ValueError):
        next_daily_slot(["09:00"], "UTC", datetime(2026, 10, 31))


def test_is_future_datetime_requires_an_aware_future_value():
    now = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    assert is_future_datetime(datetime(2026, 10, 31, 0, 1, tzinfo=timezone.utc), now)
    assert not is_future_datetime(datetime(2026, 10, 30, 23, 59, tzinfo=timezone.utc), now)
    assert not is_future_datetime(datetime(2026, 10, 31, 0, 1), now)

def test_parse_schedule_datetime_uses_selected_timezone_and_next_occurrence():
    now = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
    assert parse_schedule_datetime("09:05", "Asia/Bangkok", now=now) == datetime(
        2026, 10, 31, 2, 5, tzinfo=timezone.utc
    )

    after_slot = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    assert parse_schedule_datetime("09:05", "Asia/Bangkok", now=after_slot) == datetime(
        2026, 11, 1, 2, 5, tzinfo=timezone.utc
    )


def test_parse_schedule_datetime_validates_explicit_future_dates():
    now = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
    assert parse_schedule_datetime("25/12/2027 09:00", "Asia/Bangkok", now=now) == datetime(
        2027, 12, 25, 2, 0, tzinfo=timezone.utc
    )
    assert parse_schedule_datetime("25/12/2025 09:00", "Asia/Bangkok", now=now) is None
    assert parse_schedule_datetime("31/02/2027 09:00", "Asia/Bangkok", now=now) is None
    assert parse_schedule_datetime("24:00", "Asia/Bangkok", now=now) is None
    assert parse_schedule_datetime("09:60", "Asia/Bangkok", now=now) is None
    assert parse_schedule_datetime("09:00", "Not/A_Real_Zone", now=now) is None


def test_parse_schedule_datetime_handles_daylight_saving_gaps_and_ambiguity():
    before_spring_gap = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
    # 02:30 does not exist in New York on this date; use the next valid day.
    assert parse_schedule_datetime("02:30", "America/New_York", now=before_spring_gap) == datetime(
        2026, 3, 9, 6, 30, tzinfo=timezone.utc
    )

    before_fall_back = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    # Ambiguous 01:30 is consistently resolved to standard time (UTC-5).
    assert parse_schedule_datetime(
        "01/11/2026 01:30", "America/New_York", now=before_fall_back
    ) == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert parse_schedule_datetime(
        "08/03/2026 02:30", "America/New_York", now=before_spring_gap
    ) is None


def test_parse_schedule_datetime_requires_aware_reference_datetime():
    with pytest.raises(ValueError):
        parse_schedule_datetime("09:00", "UTC", now=datetime(2026, 10, 31))
