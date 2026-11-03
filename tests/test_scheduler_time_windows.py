from datetime import datetime, timezone
from scheduler import _is_within_window


def test_overnight_time_window_includes_late_night_and_early_morning():
    post = {
        "timezone": "Asia/Bangkok",
        "time_window": {"start": "22:00", "end": "04:00"},
    }
    assert _is_within_window(post, datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc))  # 23:00 local
    assert _is_within_window(post, datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc))  # 03:00 local


def test_overnight_time_window_excludes_daytime():
    post = {
        "timezone": "Asia/Bangkok",
        "time_window": {"start": "22:00", "end": "04:00"},
    }
    assert not _is_within_window(post, datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc))  # 10:00 local


def test_normal_time_window_includes_boundaries():
    post = {
        "timezone": "UTC",
        "time_window": {"start": "09:00", "end": "17:00"},
    }
    assert _is_within_window(post, datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc))
    assert _is_within_window(post, datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc))
    assert not _is_within_window(post, datetime(2026, 10, 31, 17, 1, tzinfo=timezone.utc))


def test_weekly_recurrence_treats_naive_mongo_datetime_as_utc_and_skips_dst_gap():
    from scheduler import _compute_next_run

    post = {
        "timezone": "America/New_York",
        "recurrence": {
            "type": "days_of_week",
            "days_of_week": [6],
            # PyMongo default datetime decoding returns this UTC value as naive.
            "next_run_at": datetime(2026, 3, 1, 7, 30),
        },
    }
    fired_at = datetime(2026, 3, 1, 7, 30, tzinfo=timezone.utc)
    assert _compute_next_run(post, fired_at) == datetime(2026, 3, 15, 6, 30, tzinfo=timezone.utc)
