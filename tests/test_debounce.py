from backend.engine import Debouncer
from backend.schemas import Observation

T, F, U = Observation.TRUE, Observation.FALSE, Observation.UNKNOWN


def feed(d: Debouncer, seq, start=0.0, step=2.0):
    """Feed observations every `step` seconds; return the timestamps at which the stable value flipped."""
    flips = []
    for i, obs in enumerate(seq):
        ts = start + i * step
        if d.update(obs, ts):
            flips.append((ts, d.stable))
    return flips


def test_needs_k_agreeing_observations():
    d = Debouncer()
    assert feed(d, [T, T]) == []
    assert d.stable is None
    d = Debouncer()
    assert feed(d, [T, T, T]) == [(4.0, True)]


def test_one_bad_frame_never_flips_or_resets():
    d = Debouncer()
    feed(d, [T, T, T])
    assert d.stable is True
    # a single FALSE among TRUEs never flips the stable value
    assert feed(d, [F, T, T, F, T], start=10) == []
    assert d.stable is True
    # a TRUE inside a FALSE run restarts it: the flip needs 3 consecutive FALSEs (36, 38, 40)
    flips = feed(d, [F, F, T, F, F, F], start=30)
    assert flips == [(40.0, False)]
    assert d.stable_since == 36.0


def test_unknown_neither_confirms_nor_breaks_a_run():
    d = Debouncer()
    assert feed(d, [T, U, U, T, U, T]) == [(10.0, True)]
    d2 = Debouncer()
    assert feed(d2, [U, U, U, U]) == []
    assert d2.stable is None


def test_flip_is_backdated_to_run_start():
    d = Debouncer()
    feed(d, [T, T, T])
    feed(d, [F, F, F], start=100)
    assert d.stable is False
    assert d.stable_since == 100.0  # first FALSE of the run, not the confirming third one


def test_min_span_blocks_bursts():
    d = Debouncer(k=3, min_span_sec=4.0)
    # three observations within one second are not enough
    assert feed(d, [T, T, T], step=0.4) == []
    assert d.update(T, 4.5) is True
    assert d.stable_since == 0.0


def test_round_trip():
    d = Debouncer()
    feed(d, [T, T, T, F])
    d2 = Debouncer.from_dict(d.to_dict())
    assert d2 == d
