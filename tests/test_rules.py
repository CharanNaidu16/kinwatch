import pytest

from backend.models.mock_spark import MockSpark
from backend.rules import mentions_health, resolve_notify, validate_parsed
from backend.schemas import Condition, Member, ParsedRule, Subject

PRIYA = Member(id=1, household_id=1, name="Priya", address="910000000001", aliases=["प्रिया"], escalation_order=0)
RAHUL = Member(id=2, household_id=1, name="Rahul", address="910000000002", aliases=["राहुल"], escalation_order=1)
MEMBERS = [PRIYA, RAHUL]


def parsed(**overrides) -> ParsedRule:
    base = {"status": "ok", "clarifying_question": None, "subject": "person", "subject_name": "Papa",
            "zone_object": "bed", "condition": "ABSENT_FROM_ZONE", "duration_sec": 600,
            "time_window": {"start": "22:00", "end": "06:00"}, "guard": None, "notify": ["me"], "language": "en"}
    return ParsedRule.model_validate({**base, **overrides})


def validate(p: ParsedRule, text: str = "tell me if Papa leaves the bed", sender: Member = PRIYA):
    return validate_parsed(p, original_text=text, members=MEMBERS, sender=sender, household_id=1)


async def parse_with_mock(text: str):
    spark = MockSpark()
    p = await spark.parse_rule(text, [m.name for m in MEMBERS], PRIYA.name)
    return validate(p, text)


# --- the three demo scenarios, end to end through the mock parser ------------------------------

async def test_main_scenario_hindi():
    v = await parse_with_mock("पापा अगर रात को उठें और दस मिनट में वापस न आएँ तो मुझे और राहुल को बताना")
    assert v.kind == "ok", v
    r = v.rule
    assert (r.subject, r.condition, r.zone_object, r.duration_sec) == (
        Subject.PERSON, Condition.ABSENT_FROM_ZONE, "bed", 600)
    assert (r.time_window.start, r.time_window.end) == ("22:00", "06:00")
    assert r.notify == [PRIYA.id, RAHUL.id]
    assert r.subject_name == "पापा" and r.language == "hi"


async def test_stove_scenario_keeps_flame_guard():
    v = await parse_with_mock("Tell me if the stove is on and nobody's in the kitchen for 5 minutes")
    assert v.kind == "ok"
    assert v.rule.condition == Condition.NO_PERSON_NEAR
    assert v.rule.guard is not None and v.rule.guard.subject == Subject.FLAME
    assert v.rule.guard.zone_object == "gas stove"


async def test_kannada_door_scenario():
    v = await parse_with_mock("ಅಪ್ಪ ವಾಕಿಂಗ್ ಮುಗಿಸಿ ಬಾಗಿಲಿಗೆ ಬಂದ್ರೆ ಹೇಳು")
    assert v.kind == "ok"
    assert (v.rule.condition, v.rule.zone_object, v.rule.language) == (Condition.PRESENT_IN_ZONE, "main door", "kn")
    assert v.rule.duration_sec == 0


async def test_spurious_guard_from_test_call_2_is_dropped():
    v = await parse_with_mock("Tell me if Papa leaves his bed at night for 10 minutes")
    assert v.kind == "ok"
    assert v.rule.guard is None
    assert any("dropped guard" in n for n in v.notes)
    assert v.rule.notify == [PRIYA.id, RAHUL.id]  # "everyone"


# --- health requests are always refused --------------------------------------------------------

async def test_health_request_is_unsupported():
    v = await parse_with_mock("Papa ki tabiyat kharab lage toh batana")
    assert v.kind == "unsupported"
    assert "सेहत" in v.question


@pytest.mark.parametrize("text", [
    "tell me if papa falls down", "बताना अगर पापा की तबीयत खराब हो", "alert me if he seems unwell",
    "ಅಪ್ಪನಿಗೆ ಜ್ವರ ಬಂದರೆ ಹೇಳು", "tell me if his blood pressure looks bad", "check if he took his medicine",
])
def test_health_terms_override_an_ok_parse(text):
    assert mentions_health(text)
    v = validate(parsed(), text)
    assert v.kind == "unsupported"


@pytest.mark.parametrize("text", [
    "tell me if Papa gets up at night", "tell me if the dog falls asleep on the sofa",
    "stove is on and nobody is in the kitchen", "ಅಪ್ಪ ಬಾಗಿಲಿಗೆ ಬಂದ್ರೆ ಹೇಳು", "will you watch the bed",
])
def test_ordinary_requests_are_not_flagged(text):
    assert not mentions_health(text)


# --- semantic fixes and clarifications ---------------------------------------------------------

def test_default_duration_for_absent_rules():
    v = validate(parsed(duration_sec=0))
    assert v.rule.duration_sec == 300
    assert any("default duration" in n for n in v.notes)


def test_no_default_duration_for_on_object():
    v = validate(parsed(subject="dog", subject_name=None, zone_object="sofa", condition="ON_OBJECT", duration_sec=0))
    assert v.rule.duration_sec == 0


def test_flame_subject_moves_into_guard():
    v = validate(parsed(subject="flame", subject_name=None, zone_object="gas stove", condition="NO_PERSON_NEAR",
                        duration_sec=300, time_window=None))
    assert v.rule.subject == Subject.PERSON
    assert v.rule.guard.subject == Subject.FLAME


def test_person_guard_on_no_person_near_is_dropped():
    v = validate(parsed(condition="NO_PERSON_NEAR", zone_object="gas stove",
                        guard={"subject": "person", "zone_object": "gas stove"}))
    assert v.rule.guard is None


@pytest.mark.parametrize("zone", ["bed and sofa", "bed, sofa", "stove + fridge", "बिस्तर और सोफ़ा"])
def test_list_zone_asks_for_one_object(zone):
    v = validate(parsed(zone_object=zone))
    assert v.kind == "clarify"
    assert "one object" in v.question


def test_zone_is_normalized():
    assert validate(parsed(zone_object="  The   Bed. ")).rule.zone_object == "the bed"


def test_long_zone_phrase_is_rejected():
    assert validate(parsed(zone_object="the big wooden bed near the window")).kind == "clarify"


def test_equal_window_bounds_ask():
    v = validate(parsed(time_window={"start": "22:00", "end": "22:00"}))
    assert v.kind == "clarify"


def test_unknown_notify_name_asks_and_lists_members():
    v = validate(parsed(notify=["me", "Sunil"]))
    assert v.kind == "clarify"
    assert "Sunil" in v.question and "Priya" in v.question and "Rahul" in v.question


def test_status_ok_without_zone_asks():
    assert validate(parsed(zone_object=None)).kind == "clarify"
    assert validate(parsed(condition="none")).kind == "clarify"


def test_needs_clarification_passes_question_through():
    v = validate(parsed(status="needs_clarification", clarifying_question="कौन सा बिस्तर?"))
    assert (v.kind, v.question) == ("clarify", "कौन सा बिस्तर?")


# --- notify resolution -------------------------------------------------------------------------

@pytest.mark.parametrize(("names", "expected"), [
    (["me"], [1]), (["मुझे"], [1]), (["ನನಗೆ"], [1]), (["Rahul"], [2]), (["rahul "], [2]),
    (["राहुल"], [2]), (["everyone"], [1, 2]), (["sabko"], [1, 2]), (["Rahul", "me", "Rahul"], [2, 1]),
])
def test_resolve_notify(names, expected):
    ids, unknown = resolve_notify(names, MEMBERS, PRIYA)
    assert unknown is None
    assert ids == expected


def test_resolve_notify_sender_is_rahul():
    ids, _ = resolve_notify(["me"], MEMBERS, RAHUL)
    assert ids == [2]
