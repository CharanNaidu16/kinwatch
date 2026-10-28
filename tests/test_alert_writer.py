"""Spark-written alerts: the checker, the template fallback, and the request shape."""
import asyncio
import json

import httpx
import pytest

from backend import templates
from backend.alert_check import banned_terms, problems
from backend.budget import Budget
from backend.config import Settings
from backend.models.meta_spark import MetaSpark
from backend.schemas import Condition, EventFacts, Subject
from tests.test_meta_adapters import _calls, _completion
from tests.test_watcher import EMPTY, IN_BED, WALKING, World, arm


def facts(condition=Condition.ABSENT_FROM_ZONE, lang="en", start="02:03", met="02:13", minutes=10, **kw) -> EventFacts:
    what = "flame on, nobody near the gas stove" if kw.pop("flame", False) else "left the bed"
    return EventFacts(rule_id=1, subject_name=kw.pop("name", "Papa"), subject=Subject.PERSON, zone_object="bed",
                      condition=condition, what=what, started_at_local=start, met_at_local=met,
                      duration_min=minutes, language=lang, **kw)


@pytest.mark.parametrize("lang", ["en", "hi", "kn"])
@pytest.mark.parametrize("condition", list(Condition))
@pytest.mark.parametrize("start, met", [("02:03", "02:13"), ("22:00", "22:10"), ("09:45", "09:55")])
def test_every_template_passes_the_checker(lang, condition, start, met):
    f = facts(condition, lang, start, met)
    assert problems(templates.alert(f), f) == []


def test_flame_template_passes():
    f = facts(Condition.NO_PERSON_NEAR, "hi", flame=True)
    assert problems(templates.alert(f), f) == []


@pytest.mark.parametrize("text, reason", [
    ("⚠️ Papa left the bed at 2:03 AM and may have fallen. Please check on him.", "banned"),
    ("⚠️ Papa left the bed at 2:15 AM and hasn't come back.", "start time"),
    ("⚠️ Papa left the bed at 2:03 AM and hasn't come back for 25 minutes.", "numbers not in the facts"),
    ("⚠️ पापा रात 2:03 बजे बिस्तर से उठे, शायद तबीयत ठीक नहीं है।", "not written in"),  # hi text for an en alert
    ("⚠️ Papa left the bed at 2:03 AM 😟", "emoji"),
    ("⚠️ Papa left the bed at 2:03 AM. " + "He has not come back yet. " * 12, "longer"),
    ("⚠️ Papa left the bed at 2:03 AM. See https://example.com", "link"),
    ("", "empty"),
])
def test_bad_english_alerts_are_rejected(text, reason):
    found = problems(text, facts())
    assert found and any(reason in p for p in found)


def test_hindi_health_words_are_rejected():
    f = facts(lang="hi")
    assert any("banned" in p for p in problems("⚠️ पापा रात 2:03 बजे उठे, शायद तबीयत खराब है।", f))
    assert problems("⚠️ पापा रात 2:03 बजे बिस्तर से उठे और 10 मिनट से वापस नहीं आए।", f) == []


def test_devanagari_numerals_count():
    assert problems("⚠️ पापा रात २:०३ बजे बिस्तर से उठे और १० मिनट से वापस नहीं आए।", facts(lang="hi")) == []


def test_banned_list_is_loaded_for_every_language():
    terms = banned_terms()
    assert "doctor" in terms["en"] and "डॉक्टर" in terms["hi"] and "ವೈದ್ಯ" in terms["kn"]


# --- Alerts._write: what the family actually gets ----------------------------------------------------

@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


async def fire(world) -> str:
    await arm(world)
    for objects in [IN_BED] * 4 + [WALKING] + [EMPTY] * 20:
        await world.see(objects)
    return next(s.text for s in world.sent(world.priya) if s.buttons and s.buttons[0].id.startswith("claim:"))


def writer_info(world) -> dict:
    row = world.storage.query_one("SELECT data_json FROM timeline WHERE kind = 'alert_sent'")
    return json.loads(row["data_json"])


async def test_a_good_spark_text_is_sent(world, monkeypatch):
    async def write(f):
        return f"⚠️ Papa got up from the bed at {templates.spoken_time(f.started_at_local, 'en')} and is not back yet."
    monkeypatch.setattr(world.router.adapters.spark, "write_alert", write)
    sent = await fire(world)
    assert sent.startswith("⚠️ Papa got up from the bed at")
    assert writer_info(world)["writer"] == "spark"


async def test_a_bad_spark_text_falls_back_to_the_template(world, monkeypatch):
    async def write(f):
        return "⚠️ Papa may have fallen. Please call a doctor."
    monkeypatch.setattr(world.router.adapters.spark, "write_alert", write)
    sent = await fire(world)
    assert sent.startswith("⚠️ Papa left the bed at")
    info = writer_info(world)
    assert info["writer"] == "template" and "banned" in info["reason"] and info["rejected"].startswith("⚠️ Papa may")


async def test_a_failing_or_slow_writer_never_holds_the_alert(world, monkeypatch):
    from backend import alerts

    monkeypatch.setattr(alerts, "ALERT_WRITE_TIMEOUT_SEC", 0.05)

    async def slow(f):
        await asyncio.sleep(5)
        return "never used"
    monkeypatch.setattr(world.router.adapters.spark, "write_alert", slow)
    sent = await fire(world)
    assert sent.startswith("⚠️ Papa left the bed at")
    assert writer_info(world) == {**writer_info(world), "writer": "template", "reason": "TimeoutError"}


# --- request shape -----------------------------------------------------------------------------------

@pytest.fixture
def live_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, use_mocks=False, meta_api_key="sk-test", data_dir=tmp_path / "data")


async def test_write_alert_request_shape(live_settings, storage, clock):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion({"text": "⚠️ Papa left the bed at 2:03 AM."}))

    spark = MetaSpark(live_settings, Budget(storage, live_settings, clock),
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await spark.write_alert(facts())
    assert out == "⚠️ Papa left the bed at 2:03 AM."
    body = seen[0]
    assert body["reasoning_effort"] == "minimal"
    assert body["response_format"]["json_schema"]["name"] == "alert_text"
    user = body["messages"][1]["content"]
    assert user.startswith("LANGUAGE: en\nFACTS: ") and "REFERENCE: ⚠️ Papa left the bed at 2:03 AM" in user
    assert '"rule_id"' not in user
    assert _calls(storage)[0]["purpose"] == "write_alert"
