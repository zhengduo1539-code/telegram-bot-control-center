from datetime import datetime, timezone
import pytest
from schedule_utils import is_future_datetime, next_daily_slot, parse_hhmm


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
