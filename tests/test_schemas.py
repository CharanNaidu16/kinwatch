import pytest
from pydantic import ValidationError

from backend.schemas import RULE_LLM_SCHEMA, Condition, ParsedRule, Subject, TimeWindow


def test_llm_schema_matches_python_enums():
    props = RULE_LLM_SCHEMA["properties"]
    assert set(props["condition"]["enum"]) == {c.value for c in Condition} | {"none"}
    assert set(props["subject"]["enum"]) == {s.value for s in Subject} | {"none"}
    assert set(RULE_LLM_SCHEMA["required"]) == set(ParsedRule.model_fields)
    assert set(props) == set(ParsedRule.model_fields)


def test_llm_schema_is_strict_everywhere():
    """Constrained decoding needs additionalProperties: false on every object (cookbook 04)."""
    def objects(node):
        if isinstance(node, dict):
            if "object" in (node.get("type") if isinstance(node.get("type"), list) else [node.get("type")]):
                yield node
            for v in node.values():
                yield from objects(v)

    for obj in objects(RULE_LLM_SCHEMA):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])


def test_time_window_validation():
    w = TimeWindow(start="22:00", end="06:00")
    assert (w.start_min, w.end_min) == (1320, 360)
    for bad in ("24:00", "7:00", "22:60", "noon"):
        with pytest.raises(ValidationError):
            TimeWindow(start=bad, end="06:00")


def test_parsed_rule_rejects_extra_fields():
    base = {"status": "ok", "clarifying_question": None, "subject": "person", "subject_name": None,
            "zone_object": "bed", "condition": "ABSENT_FROM_ZONE", "duration_sec": 600, "time_window": None,
            "guard": None, "notify": ["me"], "language": "hi"}
    assert ParsedRule.model_validate(base).zone_object == "bed"
    with pytest.raises(ValidationError):
        ParsedRule.model_validate({**base, "surprise": 1})
    with pytest.raises(ValidationError):
        ParsedRule.model_validate({**base, "duration_sec": 99999})
