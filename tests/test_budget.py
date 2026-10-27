import pytest

from backend.budget import BREAKER_ERRORS, Budget, BudgetExceeded, Usage, estimate_cost


@pytest.fixture
def budget(storage, settings, clock):
    return Budget(storage, settings, clock)


def test_cost_arithmetic():
    # test call 2's real usage: 710 in, 1702 out (1616 of them reasoning) ≈ $0.0081
    assert estimate_cost("muse-spark-1.3", Usage(input_tokens=710, output_tokens=1702)) == pytest.approx(0.0081, abs=1e-4)
    assert estimate_cost("muse-spark-1.3", Usage(input_tokens=1000, cached_input_tokens=1000)) == pytest.approx(0.00015)
    assert estimate_cost("sam-3.1", Usage(images=1000)) == pytest.approx(2.50)
    assert estimate_cost("sam-3.1", Usage(video_frames=10)) == pytest.approx(0.01)  # 50-frame minimum
    assert estimate_cost("muse-voice-transcribe-1.0", Usage(audio_sec=3600)) == pytest.approx(0.18)


def test_unpriced_model_is_refused():
    with pytest.raises(ValueError):
        estimate_cost("muse-wonder-1.0", Usage(input_tokens=1))


def test_total_cap_keeps_the_alert_reserve(budget, settings):
    settings.budget_total_usd, settings.budget_daily_usd, settings.alert_reserve_usd = 2.0, 100.0, 1.0
    budget.record("sam-3.1", "segment_subject", Usage(images=380), ok=True)  # $0.95
    with pytest.raises(BudgetExceeded, match="total cap"):
        budget.check("sam-3.1", "segment_subject", Usage(images=40))  # would pass $1.00 (= cap - reserve)
    budget.check("muse-spark-1.3", "write_alert", Usage(input_tokens=2000, output_tokens=2000))  # alerts may use it


def test_daily_cap_resets_at_ist_midnight(budget, settings, clock):
    settings.budget_daily_usd, settings.alert_reserve_usd = 0.5, 0.0
    budget.record("sam-3.1", "segment_subject", Usage(images=200), ok=True)  # $0.50 at 21:00 IST
    with pytest.raises(BudgetExceeded, match="daily cap"):
        budget.check("sam-3.1", "segment_subject", Usage(images=1))
    clock.advance(3 * 3600 + 1)  # past midnight IST
    budget.check("sam-3.1", "segment_subject", Usage(images=1))


def test_sam_rate_caps(budget, settings, clock):
    settings.sam_max_per_min, settings.sam_max_per_hour = 3, 5
    for _ in range(3):
        budget.record("sam-3.1", "segment_subject", Usage(images=1), ok=True)
    with pytest.raises(BudgetExceeded, match="per-minute"):
        budget.check("sam-3.1", "segment_subject", Usage(images=1))
    clock.advance(61)
    for _ in range(2):
        budget.record("sam-3.1", "segment_subject", Usage(images=1), ok=True)
    clock.advance(61)
    with pytest.raises(BudgetExceeded, match="per-hour"):
        budget.check("sam-3.1", "segment_subject", Usage(images=1))
    clock.advance(3600)
    budget.check("sam-3.1", "segment_subject", Usage(images=1))


def test_asr_daily_seconds(budget, settings):
    settings.asr_max_sec_per_day = 60
    budget.record("muse-voice-transcribe-1.0", "transcribe", Usage(audio_sec=50), ok=True)
    with pytest.raises(BudgetExceeded, match="transcription"):
        budget.check("muse-voice-transcribe-1.0", "transcribe", Usage(audio_sec=15))


def test_kill_switch(budget, settings):
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.kill_switch_path.write_text("stop")
    with pytest.raises(BudgetExceeded, match="kill switch"):
        budget.check("sam-3.1", "segment_subject", Usage(images=1))


def test_circuit_breaker_opens_after_repeated_errors(budget, clock):
    for _ in range(BREAKER_ERRORS):
        budget.record("sam-3.1", "segment_subject", Usage(), ok=False, error_code="500")
    with pytest.raises(BudgetExceeded, match="circuit breaker"):
        budget.check("sam-3.1", "segment_subject", Usage(images=1))
    budget.check("muse-spark-1.3", "parse_rule", Usage(input_tokens=10))  # other models unaffected
    clock.advance(301)
    budget.check("sam-3.1", "segment_subject", Usage(images=1))


async def test_guard_records_actual_usage_and_errors(budget, storage):
    async with budget.guard("muse-spark-1.3", "parse_rule", Usage(input_tokens=700, output_tokens=2000)) as call:
        call.usage = Usage(input_tokens=710, output_tokens=1702)
    row = storage.query_one("SELECT * FROM api_calls ORDER BY id DESC LIMIT 1")
    assert (row["input_tokens"], row["output_tokens"], row["ok"]) == (710, 1702, 1)

    class Boom(Exception):
        status_code = 503

    with pytest.raises(Boom):
        async with budget.guard("sam-3.1", "segment_subject", Usage(images=1)):
            raise Boom()
    row = storage.query_one("SELECT * FROM api_calls ORDER BY id DESC LIMIT 1")
    assert (row["ok"], row["error_code"], row["est_cost_usd"]) == (0, "503", 0.0)


async def test_guard_refuses_before_calling(budget, settings):
    settings.budget_total_usd = 0.0
    called = False
    with pytest.raises(BudgetExceeded):
        async with budget.guard("sam-3.1", "segment_subject", Usage(images=1)):
            called = True
    assert not called


def test_mock_calls_cost_nothing_and_skip_caps(budget, settings):
    settings.sam_max_per_min = 1
    for _ in range(5):
        budget.record("sam-3.1", "segment_subject", Usage(images=1), ok=True, mode="mock")
    assert budget.spent_usd() == 0.0
    budget.check("sam-3.1", "segment_subject", Usage(images=1))
    summary = budget.summary()
    assert summary["by_model"] == [{"model": "sam-3.1", "mode": "mock", "calls": 5, "last_hour": 5, "usd": 0.0}]
