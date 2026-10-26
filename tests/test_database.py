from datetime import datetime, timezone

from database import UsageStore


def test_current_day_is_utc():
    sample = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)
    assert UsageStore.current_day(sample) == "2026-10-26"
