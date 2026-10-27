import pytest

from backend.clock import ist_ts, local_hhmm
from backend.engine import in_window
from backend.schemas import TimeWindow

NIGHT = TimeWindow(start="22:00", end="06:00")
EVENING = TimeWindow(start="18:00", end="21:30")


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(21, 59, False), (22, 0, True), (23, 59, True), (0, 0, True), (2, 0, True), (5, 59, True),
     (6, 0, False), (12, 0, False)],
)
def test_window_crossing_midnight(hour, minute, expected):
    assert in_window(ist_ts(2026, 10, 29, hour, minute), NIGHT) is expected


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(17, 59, False), (18, 0, True), (21, 29, True), (21, 30, False)],
)
def test_same_day_window(hour, minute, expected):
    assert in_window(ist_ts(2026, 10, 29, hour, minute), EVENING) is expected


def test_no_window_means_always():
    assert in_window(ist_ts(2026, 10, 29, 3, 0), None)


def test_windows_use_ist_not_the_host_clock():
    # 20:45 UTC is 02:15 IST the next day: inside the night window regardless of the host timezone.
    from datetime import datetime, timezone

    ts = datetime(2026, 10, 29, 20, 45, tzinfo=timezone.utc).timestamp()
    assert local_hhmm(ts) == "02:15"
    assert in_window(ts, NIGHT)
