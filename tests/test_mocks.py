import io
import wave

import pytest

from backend import templates
from backend.models.mock_sam import PALETTE, MockSegmenter, synthetic_frame
from backend.models.mock_spark import MockSpark, detect_language
from backend.models.mock_transcribe import DEFAULT_TRANSCRIPT, MockTranscriber
from backend.schemas import Condition, EventFacts, Guard, Rule, Subject, TimeWindow

BED_1, BED_2 = PALETTE["bed"]
PERSON = PALETTE["person"][0]


async def test_mock_sam_finds_each_bed_and_the_person():
    frame = synthetic_frame([(BED_1, (40, 250, 300, 420)), (BED_2, (360, 250, 600, 420)),
                             (PERSON, (100, 200, 180, 330))])
    sam = MockSegmenter()
    beds = await sam.segment(frame, "bed", purpose="segment_zone")
    assert len(beds) == 2
    left = min(beds, key=lambda i: i.box.x1)
    assert abs(left.box.x1 - 40) <= 2 and abs(left.box.x2 - 300) <= 2
    people = await sam.segment(frame, "person")
    assert len(people) == 1 and people[0].mask.shape == (480, 640)
    # the person covers part of bed 1, so bed 1's mask has a hole there (SAM masks are visible pixels)
    assert not left.mask[260, 140]
    assert await sam.segment(frame, "flame") == []
    assert await sam.segment(frame, "unicorn") == []
    assert sam.calls == ["bed", "person", "flame", "unicorn"]


async def test_mock_sam_sees_nothing_in_the_dark():
    frame = synthetic_frame([(PERSON, (100, 200, 180, 330))], brightness=0.15)
    assert await MockSegmenter().segment(frame, "person") == []


@pytest.mark.parametrize(("text", "lang"), [
    ("Tell me if the dog gets on the sofa", "en"), ("पापा रात को उठें तो बताना", "hi"),
    ("papa raat ko uthe toh batana", "hi"), ("ಅಪ್ಪ ಬಂದ್ರೆ ಹೇಳು", "kn"),
])
def test_detect_language(text, lang):
    assert detect_language(text) == lang


async def test_mock_spark_heuristics_for_unseen_sentences():
    spark = MockSpark()
    bed = await spark.parse_rule("Tell me if Papa gets up from bed at night and isn't back in 15 minutes", ["Priya"], "Priya")
    assert (bed.condition, bed.zone_object, bed.duration_sec) == (Condition.ABSENT_FROM_ZONE, "bed", 900)
    assert bed.time_window == TimeWindow(start="22:00", end="06:00")
    dog = await spark.parse_rule("ಸೋಫಾ ಮೇಲೆ ನಾಯಿ ಹತ್ತಿದರೆ ಹೇಳು", ["Priya"], "Priya")
    assert (dog.subject, dog.condition, dog.language) == (Subject.DOG, Condition.ON_OBJECT, "kn")
    vague = await spark.parse_rule("keep an eye on things", ["Priya"], "Priya")
    assert vague.status == "needs_clarification"
    sofa = await spark.parse_rule("पापा रात को सोफ़े से उठें और 10 मिनट में वापस न आएँ तो मुझे बताना", ["Priya"], "Priya")
    assert (sofa.subject, sofa.condition, sofa.zone_object) == (Subject.PERSON, Condition.ABSENT_FROM_ZONE, "sofa")


async def test_mock_spark_fills_member_placeholders():
    p = await MockSpark().parse_rule(
        "पापा अगर रात को उठें और दस मिनट में वापस न आएँ तो मुझे और राहुल को बताना", ["Asha", "Vikram"], "Asha")
    assert p.notify == ["me", "Vikram"]


async def test_mock_correction():
    spark = MockSpark()
    assert (await spark.resolve_correction(b"", [], "2", current=1)).instance == 2
    two = [None, None]
    assert (await spark.resolve_correction(b"", two, "no, the other bed", current=1)).instance == 2
    assert (await spark.resolve_correction(b"", two, "नहीं, दूसरा वाला", current=2)).instance == 1
    assert (await spark.resolve_correction(b"", two, "cancel", current=1)).action == "cancel"


async def test_mock_transcriber_default_and_duration():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000 * 2)
    t = await MockTranscriber().transcribe(buf.getvalue(), ["Hindi"])
    assert t.text == DEFAULT_TRANSCRIPT and t.duration_ms == 2000


# --- templates (the fallback copy) -------------------------------------------------------------

def _rule(condition: Condition, lang: str, **kw) -> Rule:
    base = dict(household_id=1, created_by=1, original_text="x", subject=Subject.PERSON, subject_name=None,
                zone_object="bed", condition=condition, duration_sec=600, notify=[1], language=lang)
    return Rule(**{**base, **kw})


@pytest.mark.parametrize("lang", ["en", "hi", "kn"])
@pytest.mark.parametrize("condition", list(Condition))
def test_restatement_and_alert_for_every_condition_and_language(condition, lang):
    guard = Guard(subject=Subject.FLAME, zone_object="gas stove") if condition == Condition.NO_PERSON_NEAR else None
    rule = _rule(condition, lang, guard=guard, time_window=TimeWindow(start="22:00", end="06:00"))
    text = templates.restatement(rule, 1, ["Priya", "Rahul"])
    assert "Priya" in text and "Rahul" in text
    assert "10" in text  # duration and/or window hour shown
    facts = EventFacts(rule_id=1, subject_name="Papa", subject=Subject.PERSON, zone_object="bed", condition=condition,
                       what="flame on, nobody near" if guard else "left the bed", started_at_local="02:03",
                       met_at_local="02:13", duration_min=10, language=lang)
    alert = templates.alert(facts)
    assert "2:03" in alert


def test_main_scenario_restatement_hindi():
    rule = _rule(Condition.ABSENT_FROM_ZONE, "hi", subject_name="पापा", time_window=TimeWindow(start="22:00", end="06:00"))
    assert templates.restatement(rule, 1, ["Priya", "Rahul"]) == (
        "मैं रात 10 बजे से सुबह 6 बजे तक इस बिस्तर पर नज़र रखूँगा। "
        "अगर पापा बिस्तर छोड़कर 10 मिनट तक वापस नहीं आते, तो मैं Priya और Rahul को बताऊँगा। "
        "(मैं लोगों को गिनता हूँ, पहचानता नहीं।) सही है?"
    )


def test_main_scenario_on_the_sofa_uses_hindi_oblique():
    rule = _rule(Condition.ABSENT_FROM_ZONE, "hi", subject_name="पापा", zone_object="sofa",
                 time_window=TimeWindow(start="22:00", end="06:00"))
    text = templates.restatement(rule, 1, ["Priya"])
    assert "इस सोफ़े पर नज़र" in text and "सोफ़ा छोड़कर" in text
    door = _rule(Condition.PRESENT_IN_ZONE, "hi", zone_object="main door", duration_sec=0)
    assert "मुख्य दरवाज़े के पास" in templates.restatement(door, 1, ["Priya"])


def test_two_instances_ask_which_one():
    rule = _rule(Condition.ABSENT_FROM_ZONE, "en")
    assert "Which one" in templates.restatement(rule, 2, ["Priya"])


def test_spoken_time():
    assert templates.spoken_time("22:00", "en") == "10 PM"
    assert templates.spoken_time("02:03", "en") == "2:03 AM"
    assert templates.spoken_time("00:30", "en") == "12:30 AM"
    assert templates.spoken_time("06:00", "hi") == "सुबह 6"
    assert templates.spoken_time("18:42", "kn") == "ಸಂಜೆ 6:42"


def test_poor_visibility_and_no_answer_are_reported():
    facts = EventFacts(rule_id=1, subject_name="Papa", subject=Subject.PERSON, zone_object="bed",
                       condition=Condition.ABSENT_FROM_ZONE, what="left the bed", started_at_local="02:03",
                       met_at_local="02:13", duration_min=10, visibility="poor", check_in="no response", language="en")
    alert = templates.alert(facts)
    assert "couldn't see clearly" in alert and "no answer" in alert
