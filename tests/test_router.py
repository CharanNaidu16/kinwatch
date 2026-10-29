import io
import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.config import FamilyMember
from backend.main import create_app
from backend.models.mock_sam import PALETTE, synthetic_frame
from backend.router import is_no, is_yes
from tests import wa

BED1, BED2, PERSON = PALETTE["bed"][0], PALETTE["bed"][1], PALETTE["person"][0]
ONE_BED = [(BED1, (300, 250, 620, 420)), (PERSON, (340, 230, 560, 330))]

PRIYA, RAHUL, STRANGER = "910000000001", "910000000002", "919999999999"
HINDI_RULE = "पापा अगर रात को उठें और दस मिनट में वापस न आएँ तो मुझे और राहुल को बताना"


@pytest.fixture
def app(settings, storage, clock):
    settings.family_members_json = [
        FamilyMember(name="Priya", address=PRIYA, channel="mock", is_admin=True),
        FamilyMember(name="Rahul", address=RAHUL, channel="mock", aliases=["राहुल"]),
    ]
    return create_app(settings, clock=clock, storage=storage)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def post(client, *messages, statuses=None):
    token = client.app.state.settings.bts_token.get_secret_value()
    r = client.post("/dev/inbound", content=json.dumps(wa.envelope(list(messages), statuses)),
                    headers={"X-BTS-Token": token, "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()


def camera(client, objects=ONE_BED):
    token = client.app.state.settings.device_token.get_secret_value()
    r = client.post("/api/frame", content=synthetic_frame(objects), headers={"X-Device-Token": token})
    assert r.status_code == 200


def outbox(client, address=PRIYA):
    member = client.app.state.storage.member_by_address("mock", address)
    return client.app.state.messenger.to(member.id)


@pytest.mark.parametrize("word", ["yes", "Haan", "हाँ", "ok!", "ಹೌದು", "sahi hai"])
def test_yes_words(word):
    assert is_yes(word)


@pytest.mark.parametrize("word", ["no", "नहीं, दूसरा वाला", "nahi", "ಇಲ್ಲ"])
def test_no_words(word):
    assert is_no(word) and not is_yes(word)


def test_hindi_rule_is_restated_with_buttons_then_armed(client, storage):
    camera(client)
    assert post(client, wa.text(PRIYA, HINDI_RULE, "m1")) == {"ok": True, "accepted": 1}
    [confirm] = outbox(client)
    assert confirm.kind == "image" and confirm.jpeg.startswith(bytes([0xFF, 0xD8]))  # photo with the bed outlined
    assert confirm.text.startswith("मैं रात 10 बजे से सुबह 6 बजे तक इस बिस्तर पर नज़र रखूँगा।")
    assert "Priya और Rahul" in confirm.text
    rule_id = storage.query_one("SELECT id, status FROM rules")["id"]
    assert [(b.id, b.title) for b in confirm.buttons] == [(f"yes:{rule_id}", "हाँ ✓"), (f"no:{rule_id}", "नहीं ✗")]
    assert storage.query_one("SELECT kind, status FROM deliveries")["kind"] == "confirm"

    post(client, wa.button(PRIYA, f"yes:{rule_id}", msg_id="m2"))
    assert outbox(client)[-1].text == "✅ नियम चालू।"
    assert storage.query_one("SELECT status FROM rules")["status"] == "armed"
    kinds = [r["kind"] for r in storage.timeline(client.app.state.household_id)]
    assert kinds[:2] == ["rule_armed", "rule_parsed"]


def test_typed_no_declines(client, storage):
    camera(client)
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    post(client, wa.text(PRIYA, "nahi", "m2"))
    assert storage.query_one("SELECT status FROM rules")["status"] == "deleted"
    assert outbox(client)[-1].text.startswith("ठीक है, मैंने यह नियम नहीं बनाया")


def test_unrecognised_reply_gets_the_fallback(client):
    camera(client)
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    post(client, wa.text(PRIYA, "kal subah dekhte hain", "m2"))
    assert outbox(client)[-1].text == "माफ़ कीजिए, कृपया बटन दबाएँ या हाँ/नहीं लिखें।"


def test_stale_button_gets_the_fallback(client, storage):
    post(client, wa.button(PRIYA, "yes:999", msg_id="m1"))
    assert outbox(client)[-1].text.startswith("माफ़ कीजिए")
    assert storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0


def test_health_request_is_refused(client, storage):
    post(client, wa.text(PRIYA, "Papa ki tabiyat kharab lage toh batana", "m1"))
    assert outbox(client)[-1].text.startswith("मैं सेहत के बारे में नहीं बता सकता")
    assert storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0


def test_clarification_round_then_rule(client, storage):
    camera(client)
    post(client, wa.text(PRIYA, "keep an eye on things", "m1"))
    assert "What should I watch" in outbox(client)[-1].text
    post(client, wa.text(PRIYA, "tell me if Papa gets up from bed at night", "m2"))
    assert outbox(client)[-1].buttons  # restated with Yes/No
    assert storage.query_one("SELECT status FROM rules")["status"] == "awaiting_confirmation"


def test_strangers_and_duplicates_are_ignored(client, storage):
    camera(client)
    assert post(client, wa.text(STRANGER, HINDI_RULE, "m1"))["accepted"] == 0
    assert client.app.state.messenger.outbox == []
    assert post(client, wa.text(PRIYA, HINDI_RULE, "m2"))["accepted"] == 1
    assert post(client, wa.text(PRIYA, HINDI_RULE, "m2"))["accepted"] == 0  # webhook retry
    assert len(outbox(client)) == 1
    assert storage.query_one("SELECT processed_at FROM inbound_messages WHERE provider_msg_id = 'm2'")["processed_at"]
    assert storage.member_by_address("mock", PRIYA).last_inbound_at is not None


def _opus(seconds=1.0) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(16000 * seconds), dtype=np.float32), 16000, format="OGG", subtype="OPUS")
    return buf.getvalue()


def test_voice_note_is_echoed_then_parsed(client, storage):
    camera(client)
    client.app.state.messenger.media["media1"] = (_opus(), "audio/ogg")
    post(client, wa.voice(PRIYA, "media1", "m1"))
    heard, confirm = outbox(client)
    assert heard.text.startswith("मैंने सुना: \"पापा अगर रात को")  # mock transcriber's default sentence
    assert confirm.buttons
    assert storage.query_one("SELECT input_kind, transcript FROM rules")["input_kind"] == "voice"


def test_unreadable_voice_note_gets_a_polite_reply(client):
    client.app.state.messenger.media["bad"] = (b"not audio", "audio/ogg")
    post(client, wa.voice(PRIYA, "bad", "m1"))
    assert outbox(client)[-1].text.startswith("माफ़ कीजिए, मैं यह वॉइस नोट सुन नहीं पाया")


def test_dev_inbound_needs_the_bts_token(client):
    assert client.post("/dev/inbound", content=json.dumps(wa.envelope([wa.text(PRIYA, "hi")]))).status_code == 401


def test_no_camera_means_no_rule(client, storage):
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    assert outbox(client)[-1].text.startswith("कैमरा अभी तस्वीरें नहीं भेज रहा")
    assert storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0


def test_zone_not_in_view(client, storage):
    camera(client, [(PERSON, (100, 100, 200, 300))])  # no bed anywhere
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    reply = outbox(client)[-1]
    assert reply.text.startswith("मुझे कैमरे में बिस्तर नहीं दिख रहा (कैमरे में यह दिख रहा है)।")
    assert reply.kind == "image"  # the family sees what the camera sees


def test_two_beds_are_numbered_and_a_number_picks_one(client, storage):
    camera(client, [(BED1, (20, 250, 300, 420)), (BED2, (340, 250, 620, 420))])
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    ask = outbox(client)[-1]
    assert ask.kind == "image" and ask.text.startswith("फ़ोटो में 2 बिस्तर दिख रहे हैं")
    rule_id = storage.query_one("SELECT id FROM rules")["id"]
    assert [b.id for b in ask.buttons] == [f"pick:{rule_id}:1", f"pick:{rule_id}:2"]

    post(client, wa.text(PRIYA, "haan", "m2"))  # yes before choosing: asks which one
    assert outbox(client)[-1].text == "किस पर नज़र रखूँ? उसका नंबर लिखें।"
    post(client, wa.text(PRIYA, "2", "m3"))
    confirm = outbox(client)[-1]
    assert confirm.kind == "image" and confirm.text.startswith("मैं रात 10 बजे") and confirm.buttons[0].id == f"yes:{rule_id}"
    post(client, wa.button(PRIYA, f"yes:{rule_id}", msg_id="m4"))
    row = storage.query_one("SELECT status, zone_instance FROM rules")
    assert (row["status"], row["zone_instance"]) == ("armed", 2)
    assert client.app.state.watcher.runtimes[rule_id].zone.box[0] >= 330  # the right-hand bed


def test_no_the_other_one_switches_bed(client, storage):
    camera(client, [(BED1, (20, 250, 300, 420)), (BED2, (340, 250, 620, 420))])
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    rule_id = storage.query_one("SELECT id FROM rules")["id"]
    post(client, wa.button(PRIYA, f"pick:{rule_id}:1", msg_id="m2"))
    post(client, wa.text(PRIYA, "नहीं, दूसरा वाला", "m3"))  # mock Spark: "the other one"
    assert storage.query_one("SELECT zone_instance FROM rules")["zone_instance"] == 2


@pytest.mark.parametrize("message", ["check", "Check now", "camera", "Now can u see", "kya dikh raha hai"])
def test_camera_view_command_sends_the_latest_frame(client, storage, message):
    camera(client)
    post(client, wa.text(PRIYA, message, "m1"))
    reply = outbox(client)[-1]
    assert reply.kind == "image" and reply.text.startswith(("This is what the camera sees", "कैमरे में अभी"))
    assert storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0
    assert client.app.state.router.adapters.segmenter.calls == []  # free: no SAM


def test_camera_view_warns_about_an_upright_picture(client):
    token = client.app.state.settings.device_token.get_secret_value()
    client.post("/api/frame", content=synthetic_frame(ONE_BED, size=(360, 640)), headers={"X-Device-Token": token})
    post(client, wa.text(PRIYA, "check", "m1"))
    assert "↻" in outbox(client)[-1].text
