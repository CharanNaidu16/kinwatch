"""Time source. Everything that needs "now" takes a Clock, so replay tests can run a night in milliseconds."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since the epoch (UTC)."""
        ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    def __init__(self, start: float) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += seconds
        return self._t

    def set(self, t: float) -> None:
        self._t = float(t)


def local_dt(ts: float, tz: ZoneInfo = IST) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def local_hhmm(ts: float, tz: ZoneInfo = IST) -> str:
    return local_dt(ts, tz).strftime("%H:%M")


def minute_of_day(ts: float, tz: ZoneInfo = IST) -> int:
    d = local_dt(ts, tz)
    return d.hour * 60 + d.minute


def local_day_start(ts: float, tz: ZoneInfo = IST) -> float:
    """Epoch seconds of local midnight for the day containing ts."""
    d = local_dt(ts, tz)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def ist_ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> float:
    """Epoch seconds for a wall-clock time in IST (handy in tests and scripts)."""
    return datetime(year, month, day, hour, minute, second, tzinfo=IST).timestamp()
