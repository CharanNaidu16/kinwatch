"""Temporal engine: plain Python, no AI (PLAN.md §3.5).

This file holds the pieces the per-rule state machine is built from: time windows in IST and
the debouncer that makes one bad frame unable to trigger or reset a rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from backend.clock import IST, minute_of_day
from backend.schemas import Observation, TimeWindow

DEBOUNCE_K = 3  # consecutive agreeing observations needed to flip the stable value
DEBOUNCE_MIN_SPAN_SEC = 4.0  # ...spanning at least this long


def in_window(ts: float, window: TimeWindow | None) -> bool:
    """True if ts falls inside the daily IST window. None means always. [start, end) semantics."""
    if window is None:
        return True
    m = minute_of_day(ts, IST)
    s, e = window.start_min, window.end_min
    if s < e:
        return s <= m < e
    return m >= s or m < e  # crosses midnight


@dataclass
class Debouncer:
    """Turns noisy per-frame observations into a stable boolean.

    - The stable value flips only after `k` consecutive agreeing observations spanning `min_span_sec`.
    - UNKNOWN neither confirms nor breaks a run.
    - When a flip is accepted, `stable_since` is backdated to the first observation of the run,
      so debouncing never delays a rule's timer.
    """

    k: int = DEBOUNCE_K
    min_span_sec: float = DEBOUNCE_MIN_SPAN_SEC
    stable: bool | None = None  # None until the first run is confirmed
    stable_since: float | None = None
    run_value: bool | None = None
    run_count: int = 0
    run_first_ts: float | None = None
    last_ts: float | None = None

    def update(self, obs: Observation, ts: float) -> bool:
        """Feed one observation. Returns True if the stable value changed."""
        if obs == Observation.UNKNOWN:
            return False
        value = obs == Observation.TRUE
        self.last_ts = ts
        if value == self.run_value:
            self.run_count += 1
        else:
            self.run_value, self.run_count, self.run_first_ts = value, 1, ts
        if (
            value != self.stable
            and self.run_count >= self.k
            and self.run_first_ts is not None
            and ts - self.run_first_ts >= self.min_span_sec
        ):
            self.stable = value
            self.stable_since = self.run_first_ts
            return True
        return False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Debouncer:
        return cls(**d)
