"""API budget guard (PLAN.md §3.11).

Every real Meta Model API call goes through `Budget.guard()`: it refuses calls that would break a
cap, records actual usage, and opens a circuit breaker after repeated errors. Mock calls can be
recorded with mode="mock" so the behind-the-scenes page shows them, but they never cost anything.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from backend.clock import Clock, local_day_start
from backend.config import Settings
from backend.storage import Storage

# USD prices from dev.meta.ai/docs/pricing-rate-limits (read 26 Oct 2026). Standard mode.
_SPARK_STANDARD = {"input": 1.25e-6, "cached_input": 0.15e-6, "output": 4.25e-6}
_SPARK_CONTRIBUTOR = {"input": 0.10e-6, "cached_input": 0.002e-6, "output": 0.20e-6}
PRICES: dict[str, dict[str, float]] = {
    "muse-spark-1.3": _SPARK_STANDARD,
    "muse-spark-1.2": _SPARK_STANDARD,
    "muse-spark-1.1": _SPARK_STANDARD,
    "muse-spark-1.3-contributor": _SPARK_CONTRIBUTOR,
    "muse-spark-1.2-contributor": _SPARK_CONTRIBUTOR,
    "sam-3.1": {"image": 2.50 / 1000, "video_frame": 0.20 / 1000, "video_min_frames": 50},
    "muse-voice-transcribe-1.0": {"audio_sec": 0.18 / 3600},
}

# These may use the alert reserve; everything else stops before touching it.
ALERT_PURPOSES = frozenset({"confirm_event", "write_alert"})

BREAKER_ERRORS = 5
BREAKER_OPEN_SEC = 300


def model_family(model: str) -> str:
    if model.startswith("muse-spark"):
        return "spark"
    if model.startswith("sam"):
        return "sam"
    if model.startswith("muse-voice-transcribe"):
        return "asr"
    raise ValueError(f"no pricing family for model {model!r}")


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0  # subset of input_tokens billed at the cached rate
    output_tokens: int = 0  # reasoning tokens count as output
    images: int = 0
    video_frames: int = 0
    audio_sec: float = 0.0


def estimate_cost(model: str, usage: Usage) -> float:
    if model not in PRICES:
        raise ValueError(f"no price for model {model!r}; refusing to call an unpriced model")
    p = PRICES[model]
    family = model_family(model)
    if family == "spark":
        uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
        return (uncached * p["input"] + usage.cached_input_tokens * p["cached_input"]
                + usage.output_tokens * p["output"])
    if family == "sam":
        frames = max(usage.video_frames, int(p["video_min_frames"])) if usage.video_frames else 0
        return usage.images * p["image"] + frames * p["video_frame"]
    return usage.audio_sec * p["audio_sec"]


class BudgetExceeded(Exception):
    """The call was refused before it was made. Callers treat it as an UNKNOWN observation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class BudgetCall:
    """Yielded by guard(); the adapter sets `usage` from the API response."""

    usage: Usage | None = None


@dataclass
class _Breaker:
    consecutive_errors: int = 0
    open_until: float = 0.0


@dataclass
class Budget:
    storage: Storage
    settings: Settings
    clock: Clock
    _breakers: dict[str, _Breaker] = field(default_factory=dict)

    # --- queries --------------------------------------------------------------------------------

    def spent_usd(self, since: float | None = None) -> float:
        sql = "SELECT COALESCE(SUM(est_cost_usd), 0) AS s FROM api_calls WHERE mode = 'live'"
        params: tuple = ()
        if since is not None:
            sql += " AND ts >= ?"
            params = (since,)
        row = self.storage.query_one(sql, params)
        return float(row["s"]) if row else 0.0

    def _count(self, family: str, since: float) -> int:
        models = [m for m in PRICES if model_family(m) == family]
        marks = ",".join("?" * len(models))
        row = self.storage.query_one(
            f"SELECT COUNT(*) AS n FROM api_calls WHERE mode = 'live' AND ts >= ? AND model IN ({marks})",
            (since, *models),
        )
        return int(row["n"]) if row else 0

    def _audio_sec(self, since: float) -> float:
        row = self.storage.query_one(
            "SELECT COALESCE(SUM(audio_sec), 0) AS s FROM api_calls WHERE mode = 'live' AND ts >= ?", (since,)
        )
        return float(row["s"]) if row else 0.0

    # --- the gate -------------------------------------------------------------------------------

    def check(self, model: str, purpose: str, estimate: Usage) -> None:
        """Raise BudgetExceeded if this call must not be made."""
        s = self.settings
        now = self.clock.now()
        if s.kill_switch_path.exists():
            raise BudgetExceeded(f"kill switch {s.kill_switch_path.name} present")
        breaker = self._breakers.get(model)
        if breaker and breaker.open_until > now:
            raise BudgetExceeded(f"circuit breaker open for {model} ({int(breaker.open_until - now)}s left)")

        cost = estimate_cost(model, estimate)
        reserve = 0.0 if purpose in ALERT_PURPOSES else s.alert_reserve_usd
        total = self.spent_usd()
        if total + cost > s.budget_total_usd - reserve:
            raise BudgetExceeded(f"total cap: ${total:.4f} spent of ${s.budget_total_usd} (reserve ${reserve})")
        today = self.spent_usd(since=local_day_start(now))
        if today + cost > s.budget_daily_usd - reserve:
            raise BudgetExceeded(f"daily cap: ${today:.4f} spent of ${s.budget_daily_usd} (reserve ${reserve})")

        family = model_family(model)
        if family == "sam":
            if self._count("sam", now - 60) >= s.sam_max_per_min:
                raise BudgetExceeded(f"SAM per-minute cap ({s.sam_max_per_min})")
            if self._count("sam", now - 3600) >= s.sam_max_per_hour:
                raise BudgetExceeded(f"SAM per-hour cap ({s.sam_max_per_hour})")
        elif family == "spark":
            if self._count("spark", now - 3600) >= s.spark_max_per_hour:
                raise BudgetExceeded(f"Spark per-hour cap ({s.spark_max_per_hour})")
        elif family == "asr":
            if self._audio_sec(local_day_start(now)) + estimate.audio_sec > s.asr_max_sec_per_day:
                raise BudgetExceeded(f"transcription daily cap ({s.asr_max_sec_per_day}s)")

    def record(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        ok: bool,
        latency_ms: int | None = None,
        error_code: str | None = None,
        mode: str = "live",
    ) -> None:
        cost = estimate_cost(model, usage) if mode == "live" else 0.0
        self.storage.execute(
            "INSERT INTO api_calls (ts, model, purpose, input_tokens, cached_input_tokens, output_tokens, images,"
            " video_frames, audio_sec, est_cost_usd, latency_ms, ok, error_code, mode)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.clock.now(), model, purpose, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens,
             usage.images, usage.video_frames, usage.audio_sec, cost, latency_ms, int(ok), error_code, mode),
        )
        if mode != "live":
            return
        breaker = self._breakers.setdefault(model, _Breaker())
        if ok:
            breaker.consecutive_errors = 0
        else:
            breaker.consecutive_errors += 1
            if breaker.consecutive_errors >= BREAKER_ERRORS:
                breaker.open_until = self.clock.now() + BREAKER_OPEN_SEC
                breaker.consecutive_errors = 0

    @asynccontextmanager
    async def guard(self, model: str, purpose: str, estimate: Usage) -> AsyncIterator[BudgetCall]:
        """Check caps, let the adapter make the call, then record what it actually used.

        On error the attempt is recorded with the usage the adapter set (if any), else zero cost.
        """
        self.check(model, purpose, estimate)
        call = BudgetCall()
        t0 = time.perf_counter()
        try:
            yield call
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            self.record(model, purpose, call.usage or Usage(), ok=False,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        error_code=str(code) if code is not None else type(exc).__name__)
            raise
        self.record(model, purpose, call.usage if call.usage is not None else estimate, ok=True,
                    latency_ms=int((time.perf_counter() - t0) * 1000))

    # --- for /api/budget ------------------------------------------------------------------------

    def summary(self) -> dict:
        s = self.settings
        now = self.clock.now()
        rows = self.storage.query(
            "SELECT model, mode, COUNT(*) AS n, SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS last_hour,"
            " COALESCE(SUM(est_cost_usd), 0) AS usd FROM api_calls GROUP BY model, mode ORDER BY model",
            (now - 3600,),
        )
        return {
            "mode": "MOCK" if s.use_mocks else "LIVE",
            "total_usd": round(self.spent_usd(), 4),
            "today_usd": round(self.spent_usd(since=local_day_start(now)), 4),
            "caps": {
                "total_usd": s.budget_total_usd, "daily_usd": s.budget_daily_usd,
                "alert_reserve_usd": s.alert_reserve_usd, "sam_per_hour": s.sam_max_per_hour,
                "sam_per_min": s.sam_max_per_min, "spark_per_hour": s.spark_max_per_hour,
                "asr_sec_per_day": s.asr_max_sec_per_day,
            },
            "by_model": [
                {"model": r["model"], "mode": r["mode"], "calls": r["n"], "last_hour": r["last_hour"],
                 "usd": round(r["usd"], 4)} for r in rows
            ],
            "kill_switch": s.kill_switch_path.exists(),
            "breakers_open": sorted(m for m, b in self._breakers.items() if b.open_until > now),
        }
