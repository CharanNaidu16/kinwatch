import json

from backend.recorder import Recorder, fill, scrub


def test_scrub_and_fill_round_trip():
    names = [["Asha", "आशा"], ["Vikram", "विक्रम"]]
    text = "मुझे और विक्रम को बताना, asha too"
    scrubbed = scrub(text, names)
    assert scrubbed == "मुझे और {MEMBER:2} को बताना, {MEMBER:1} too"
    assert fill(scrubbed, ["Asha", "Vikram"]) == "मुझे और Vikram को बताना, Asha too"
    assert fill("{MEMBER:9}", ["Asha"]) == "{MEMBER:9}"


def test_recorder_never_writes_member_names(tmp_path):
    rec = Recorder([["Asha"], ["Vikram", "विक्रम"]], root=tmp_path)
    rec.spark_parse("tell me and विक्रम", {"notify": ["Asha", "Vikram"], "status": "ok"})
    rec.spark_parse("tell me and विक्रम", {"notify": ["Asha"], "status": "ok"})  # same text replaces
    rec.asr(b"wav", "मुझे और विक्रम को बताना")
    blob = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert "Asha" not in blob and "Vikram" not in blob and "विक्रम" not in blob
    entries = json.loads((tmp_path / "spark_parse_rule.json").read_text(encoding="utf-8"))
    assert entries == [{"text": "tell me and {MEMBER:2}", "response": {"notify": ["{MEMBER:1}"], "status": "ok"}}]
