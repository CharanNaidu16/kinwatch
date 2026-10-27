"""Temporal engine: plain Python, no AI (PLAN.md §3.5).

Time windows in IST, the debouncer that makes one bad frame unable to trigger or reset a rule, and
the per-rule state machine built from them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Literal

from backend.clock import IST, minute_of_day
from backend.schemas import Condition, Observation, TimeWindow

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


# --- Per-rule state machine (PLAN.md §3.5) -----------------------------------------------------

COOLDOWN_SEC = 600  # after an occurrence ends, wait before a new one can start


class Phase(str, Enum):
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    WAITING_BASELINE = "WAITING_BASELINE"  # ABSENT rules: learn how many are in the zone first
    WATCHING = "WATCHING"
    PENDING = "PENDING"  # condition holds; timer running since `since`
    FIRED = "FIRED"  # one event per occurrence; waits for the condition to end


@dataclass(frozen=True)
class Reading:
    """What vision saw for one rule in one analysed frame."""

    count: int | None  # subjects in / on / near the zone; None = couldn't see (UNKNOWN)
    guard: bool | None = None  # the guard holds (e.g. flame on); None = unknown or no guard


@dataclass(frozen=True)
class Transition:
    kind: Literal["window_open", "window_closed", "baseline", "pending", "returned", "fired", "over"]
    ts: float
    data: dict = field(default_factory=dict)


@dataclass
class RuleEngine:
    """One rule's timing. Fed observations from vision; never decides anything with a model."""

    condition: Condition
    duration_sec: float  # already divided by TIME_SCALE in demo mode
    window: TimeWindow | None = None
    has_guard: bool = False
    cooldown_sec: float = COOLDOWN_SEC

    phase: Phase = Phase.OUT_OF_WINDOW
    debouncer: Debouncer = field(default_factory=Debouncer)
    presence: Debouncer = field(default_factory=Debouncer)  # baseline learning for ABSENT rules
    baseline: int | None = None
    since: float | None = None
    unknown_sec: float = 0.0
    last_ts: float | None = None
    last_obs: Observation | None = None
    cooldown_until: float | None = None
    fired_at: float | None = None

    @property
    def settling(self) -> bool:
        """A change was seen but isn't confirmed yet: vision should look again soon, not at heartbeat pace."""
        d = self.presence if self.phase == Phase.WAITING_BASELINE else self.debouncer
        return d.run_value is not None and d.run_value != d.stable

    def observation(self, reading: Reading) -> Observation:
        if reading.count is None:
            return Observation.UNKNOWN
        c = self.condition
        if c in (Condition.PRESENT_IN_ZONE, Condition.ON_OBJECT):
            holds = reading.count >= 1
        elif c == Condition.ABSENT_FROM_ZONE:
            if self.baseline is None:
                return Observation.UNKNOWN
            holds = reading.count < self.baseline
        else:  # NO_PERSON_NEAR
            if self.has_guard and reading.guard is None:
                return Observation.UNKNOWN
            holds = reading.count == 0 and (not self.has_guard or bool(reading.guard))
        return Observation.TRUE if holds else Observation.FALSE

    def observe(self, ts: float, reading: Reading) -> list[Transition]:
        out = self.tick(ts)
        if self.phase == Phase.OUT_OF_WINDOW:
            return out
        if self.phase == Phase.PENDING and self.last_obs == Observation.UNKNOWN and self.last_ts is not None:
            self.unknown_sec += ts - self.last_ts

        if self.phase == Phase.WAITING_BASELINE:
            seen = Observation.UNKNOWN if reading.count is None else (
                Observation.TRUE if reading.count >= 1 else Observation.FALSE)
            if self.presence.update(seen, ts) and self.presence.stable:
                # Fixed for the night: a stray extra detection must never raise it (that would
                # turn a normal count into "someone left").
                self.baseline = max(reading.count or 1, 1)
                self.phase = Phase.WATCHING
                out.append(Transition("baseline", ts, {"count": self.baseline}))
            self.last_obs, self.last_ts = seen, ts
            return out

        obs = self.observation(reading)
        flipped = self.debouncer.update(obs, ts)
        self.last_obs, self.last_ts = obs, ts
        if flipped and not self.debouncer.stable:
            if self.phase == Phase.PENDING:
                self.phase = Phase.WATCHING
                out.append(Transition("returned", ts, {"after_sec": ts - (self.since or ts)}))
                self.since = None
            elif self.phase == Phase.FIRED:
                self.phase = Phase.WATCHING
                self.cooldown_until = ts + self.cooldown_sec
                out.append(Transition("over", ts))
        out += self.tick(ts)
        return out

    def tick(self, ts: float) -> list[Transition]:
        """Time-driven changes: window edges, cooldown end, the timer reaching duration."""
        out: list[Transition] = []
        inside = in_window(ts, self.window)
        if not inside and self.phase != Phase.OUT_OF_WINDOW:
            out.append(Transition("window_closed", ts, {"was": self.phase.value}))
            self._reset(Phase.OUT_OF_WINDOW)
            return out
        if inside and self.phase == Phase.OUT_OF_WINDOW:
            self._reset(Phase.WAITING_BASELINE if self.condition == Condition.ABSENT_FROM_ZONE else Phase.WATCHING)
            out.append(Transition("window_open", ts))
        if (self.phase == Phase.WATCHING and self.debouncer.stable
                and (self.cooldown_until is None or ts >= self.cooldown_until)):
            self.phase = Phase.PENDING
            self.since = max(self.debouncer.stable_since or ts, self.cooldown_until or 0.0)
            self.unknown_sec = 0.0
            out.append(Transition("pending", ts, {"since": self.since}))
        if self.phase == Phase.PENDING and self.since is not None and ts - self.since >= self.duration_sec:
            self.phase = Phase.FIRED
            self.fired_at = ts
            pending = max(ts - self.since, 1e-9)
            out.append(Transition("fired", ts, {"since": self.since,
                                                "visibility": "poor" if self.unknown_sec / pending > 0.5 else "good"}))
        return out

    def _reset(self, phase: Phase) -> None:
        self.phase = phase
        self.debouncer = Debouncer()
        self.presence = Debouncer()
        self.baseline = None
        self.since = None
        self.unknown_sec = 0.0
        self.last_obs = None
        self.cooldown_until = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k not in ("debouncer", "presence", "window")}
        d.update(phase=self.phase.value, condition=self.condition.value,
                 last_obs=self.last_obs.value if self.last_obs else None,
                 debouncer=self.debouncer.to_dict(), presence=self.presence.to_dict(),
                 window=self.window.model_dump() if self.window else None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RuleEngine:
        return cls(**{**d, "phase": Phase(d["phase"]), "condition": Condition(d["condition"]),
                      "last_obs": Observation(d["last_obs"]) if d.get("last_obs") else None,
                      "debouncer": Debouncer.from_dict(d["debouncer"]), "presence": Debouncer.from_dict(d["presence"]),
                      "window": TimeWindow(**d["window"]) if d.get("window") else None})
