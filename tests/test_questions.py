"""Ask the camera: question detection, the answer, and the photo. Mocks only."""
import json

import httpx
import pytest

from backend.budget import Budget
from backend.config import Settings
from backend.models.base import Question
from backend.models.meta_spark import MetaSpark
from backend.questions import sentence
from backend.router import is_question
from backend.schemas import Subject
from tests.test_meta_adapters import _calls, _completion
from tests.test_watcher import BED, EMPTY, IN_BED, PERSON, World, arm

SOFA_SIDE = [(BED, (300, 250, 620, 420)), (PERSON, (40, 100, 140, 400))]  # someone in the room, not on it


@pytest.fixture
def world(settings, storage, clock):
    settings.zone_concepts_json = {"sofa": "bed"}  # the mock SAM's furniture colour is "bed"
    return World(settings, storage, clock)


@pytest.mark.parametrize("message", [
    "Is Papa on the sofa?", "is anyone in the room", "पापा सोफ़े पर हैं?", "क्या सोफ़े पर कोई है", "ಅಪ್ಪ ಸೋಫಾ ಮೇಲೆ ಇದ್ದಾರಾ",
    "Where is the dog?", "Is the stove on?",
])
def test_questions_are_recognised(message):
    assert is_question(message)


@pytest.mark.parametrize("message", [
    "Tell me if Papa gets up from the sofa and isn't back in 1 minute",
    "Can you tell me if Papa leaves the bed?",
    "पापा अगर रात को उठें तो मुझे बताना?",
    "ಅಪ್ಪ ಬಾಗಿಲಿಗೆ ಬಂದ್ರೆ ಹೇಳು",
    "please check the sofa if father doesn't return in a minute send me an alert",
])
def test_rules_are_not_questions(message):
    assert not is_question(message)


def q(**kw) -> Question:
    base = dict(kind="question", subject=Subject.PERSON, subject_name=None, zone_object="sofa", relation="on",
                language="en")
    return Question(**{**base, **kw})


def test_sentences():
    assert sentence(q(), 1, "18:02") == "Yes. Right now (6:02 PM) I can see 1 person on the sofa."
    assert sentence(q(), 0, "18:02") == "No. Right now (6:02 PM) I can't see anyone on the sofa."
    assert sentence(q(zone_object=None, relation="visible"), 0, "18:02") == \
        "No. Right now (6:02 PM) I can't see anyone in the camera view."
    assert sentence(q(subject_name="Papa"), 2, "18:02").endswith("I can't tell who it is, only how many people I see.")
    assert sentence(q(language="hi"), 1, "18:02") == "हाँ। अभी (शाम 6:02) सोफ़े पर 1 व्यक्ति दिख रहा है।"
    assert sentence(q(language="hi"), 0, "18:02") == "नहीं। अभी (शाम 6:02) सोफ़े पर कोई नहीं दिख रहा।"
    assert sentence(q(language="kn"), 0, "18:02").startswith("ಇಲ್ಲ. ಈಗ (")
    assert sentence(q(subject=Subject.DOG, relation="on"), 0, "18:02") == \
        "No. Right now (6:02 PM) I can't see a dog on the sofa."


async def test_yes_with_an_outlined_photo(world):
    await world.see(IN_BED)
    calls = len(world.router.adapters.segmenter.calls)
    await world.say(world.priya, "Is Papa on the sofa?")
    reply = world.sent(world.priya)[-1]
    assert reply.kind == "image" and reply.text.startswith("Yes. Right now (")
    assert "1 person on the sofa" in reply.text and "can't tell who it is" in reply.text
    assert len(world.router.adapters.segmenter.calls) - calls == 2  # the sofa, and people
    assert world.storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0  # a question is never a rule
    entry = world.storage.query_one("SELECT data_json FROM timeline WHERE kind = 'question_answered'")
    data = json.loads(entry["data_json"])
    assert data["count"] == 1 and data["parsed"]["relation"] == "on"


async def test_no_when_the_person_is_elsewhere(world):
    await world.see(SOFA_SIDE)
    await world.say(world.priya, "Is anyone on the sofa?")
    assert world.sent(world.priya)[-1].text.startswith("No. Right now (")


async def test_anyone_in_view_needs_one_sam_call(world):
    await world.see(SOFA_SIDE)
    calls = len(world.router.adapters.segmenter.calls)
    await world.say(world.priya, "Is anyone there?")
    assert world.sent(world.priya)[-1].text.startswith("Yes. Right now (")
    assert len(world.router.adapters.segmenter.calls) - calls == 1


async def test_hindi_question_gets_a_hindi_answer(world):
    await world.see(EMPTY)
    await world.say(world.priya, "पापा सोफ़े पर हैं?")
    assert world.sent(world.priya)[-1].text.startswith("नहीं। अभी (")


async def test_object_not_in_view(world):
    await world.see([(PERSON, (40, 100, 140, 400))])
    await world.say(world.priya, "Is Papa on the sofa?")
    reply = world.sent(world.priya)[-1]
    assert reply.text.startswith("I can't see a sofa in the camera view right now.") and reply.kind == "image"


async def test_health_questions_are_not_answered(world):
    await world.see(IN_BED)
    await world.say(world.priya, "Is Papa okay?")
    reply = world.sent(world.priya)[-1]
    assert reply.text.startswith("I can only tell you what the camera sees right now")
    assert len(world.router.adapters.segmenter.calls) == 0


async def test_no_camera_no_spend(world):
    await world.say(world.priya, "Is Papa on the sofa?")
    assert world.sent(world.priya)[-1].text.startswith("The camera isn't sending pictures")


async def test_a_question_does_not_disturb_an_armed_rule(world):
    rule_id = await arm(world)
    await world.see(IN_BED)
    await world.say(world.priya, "Is Papa on the sofa?")
    assert world.storage.query_one("SELECT status FROM rules WHERE id = ?", (rule_id,))["status"] == "armed"


@pytest.fixture
def live_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, use_mocks=False, meta_api_key="sk-test", data_dir=tmp_path / "data")


async def test_parse_question_request_shape(live_settings, storage, clock):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion({"kind": "question", "subject": "person", "subject_name": "पापा",
                                                     "zone_object": "sofa", "relation": "on", "language": "hi"}))

    spark = MetaSpark(live_settings, Budget(storage, live_settings, clock),
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await spark.parse_question("पापा सोफ़े पर हैं?")
    assert out == Question("question", Subject.PERSON, "पापा", "sofa", "on", "hi")
    assert seen[0]["reasoning_effort"] == "minimal"
    assert seen[0]["response_format"]["json_schema"]["name"] == "camera_question"
    assert _calls(storage)[0]["purpose"] == "parse_question"
