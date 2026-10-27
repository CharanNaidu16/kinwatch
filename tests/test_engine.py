"""The per-rule state machine on synthetic reading streams (PLAN.md §3.5, §10)."""
from backend.clock import ist_ts
from backend.engine import Phase, Reading, RuleEngine
from backend.schemas import Condition, TimeWindow

NIGHT = TimeWindow(start="22:00", end="06:00")
IN_BED, EMPTY, BLIND = Reading(1), Reading(0), Reading(None)


def run(engine: RuleEngine, start: float, readings: list[Reading], step: float = 5.0) -> tuple[list[str], float]:
    kinds, t = [], start
    for r in readings:
        kinds += [x.kind for x in engine.observe(t, r)]
        t += step
    return kinds, t


def absent(duration=600.0, window=NIGHT) -> RuleEngine:
    return RuleEngine(condition=Condition.ABSENT_FROM_ZONE, duration_sec=duration, window=window)


def test_leaving_and_returning_in_time_never_fires():
    e = absent()
    kinds, t = run(e, ist_ts(2026, 10, 29, 22, 30), [IN_BED] * 4 + [EMPTY] * 12 + [IN_BED] * 4)
    assert kinds == ["window_open", "baseline", "pending", "returned"]
    assert e.phase == Phase.WATCHING


def test_leaving_for_too_long_fires_exactly_once_with_backdated_start():
    e = absent(duration=60)
    start = ist_ts(2026, 10, 29, 23, 0)
    kinds, t = run(e, start, [IN_BED] * 4 + [EMPTY] * 40)
    assert kinds.count("fired") == 1 and kinds.count("pending") == 1
    left_at = start + 4 * 5  # first EMPTY reading
    assert e.since == left_at and e.fired_at == left_at + 60  # debouncing adds no delay
    more, t = run(e, t, [IN_BED] * 4)
    assert more == ["over"] and e.phase == Phase.WATCHING


def test_one_bad_frame_changes_nothing():
    e = absent(duration=30)
    kinds, _ = run(e, ist_ts(2026, 10, 29, 23, 0), [IN_BED] * 4 + [EMPTY] + [IN_BED] * 3 + [EMPTY, IN_BED] * 3)
    assert kinds == ["window_open", "baseline"]


def test_unknown_neither_triggers_nor_resets_and_is_reported():
    e = absent(duration=60)
    kinds, _ = run(e, ist_ts(2026, 10, 29, 23, 0), [IN_BED] * 4 + [EMPTY] * 3 + [BLIND] * 12 + [EMPTY])
    assert kinds[-1] == "fired"
    assert BLIND not in [] and e.unknown_sec > 0.5 * (e.fired_at - e.since)


def test_two_in_bed_one_leaves_still_triggers():
    e = absent(duration=30)
    kinds, _ = run(e, ist_ts(2026, 10, 29, 23, 0), [Reading(2)] * 4 + [Reading(1)] * 12)
    assert e.baseline == 2 and "fired" in kinds


def test_window_crossing_midnight_closes_at_six():
    e = absent(duration=600)
    run(e, ist_ts(2026, 10, 30, 5, 50), [IN_BED] * 4 + [EMPTY] * 4)
    assert e.phase == Phase.PENDING
    assert [t.kind for t in e.tick(ist_ts(2026, 10, 30, 6, 0))] == ["window_closed"]
    assert e.phase == Phase.OUT_OF_WINDOW
    assert e.tick(ist_ts(2026, 10, 30, 12, 0)) == []  # daytime: stays out
    assert [t.kind for t in e.tick(ist_ts(2026, 10, 30, 22, 0))] == ["window_open"]
    assert e.phase == Phase.WAITING_BASELINE


def test_fires_on_tick_even_without_new_frames():
    e = absent(duration=60, window=None)
    _, t = run(e, ist_ts(2026, 10, 29, 23, 0), [IN_BED] * 4 + [EMPTY] * 3)
    assert e.phase == Phase.PENDING
    assert [x.kind for x in e.tick(e.since + 61)] == ["fired"]


def test_cooldown_after_an_occurrence():
    e = absent(duration=10, window=None)
    e.cooldown_sec = 120
    kinds, t = run(e, ist_ts(2026, 10, 29, 23, 0), [IN_BED] * 4 + [EMPTY] * 6 + [IN_BED] * 3 + [EMPTY] * 6)
    assert kinds.count("fired") == 1  # the second absence starts inside the cooldown
    later, _ = run(e, t + 200, [EMPTY])
    assert "fired" in later or e.phase == Phase.PENDING


def test_present_with_zero_duration_fires_on_the_stable_flip():
    e = RuleEngine(condition=Condition.PRESENT_IN_ZONE, duration_sec=0)
    kinds, _ = run(e, ist_ts(2026, 10, 29, 18, 0), [EMPTY] * 3 + [IN_BED] * 3)
    assert kinds == ["window_open", "pending", "fired"]


def test_stove_needs_flame_and_nobody_near():
    e = RuleEngine(condition=Condition.NO_PERSON_NEAR, duration_sec=30, has_guard=True)
    start = ist_ts(2026, 10, 29, 19, 0)
    kinds, t = run(e, start, [Reading(0, guard=False)] * 10)  # flame off, nobody there: fine
    assert "pending" not in kinds
    kinds, t = run(e, t, [Reading(0, guard=None)] * 10)  # flame unknown: no guessing
    assert "pending" not in kinds
    kinds, _ = run(e, t, [Reading(0, guard=True)] * 12)
    assert "fired" in kinds


def test_state_survives_a_restart_mid_pending():
    e = absent(duration=600)
    _, t = run(e, ist_ts(2026, 10, 29, 23, 0), [IN_BED] * 4 + [EMPTY] * 4)
    restored = RuleEngine.from_dict(e.to_dict())
    assert restored.phase == Phase.PENDING and restored.since == e.since and restored.baseline == 1
    assert [x.kind for x in restored.tick(e.since + 600)] == ["fired"]
