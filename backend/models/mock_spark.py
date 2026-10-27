"""Mock Muse Spark: fixture answers for known sentences, keyword heuristics otherwise, templates for writing.

Fixture notify lists use "me", "everyone" or "{MEMBER:n}" (1-based, household order), so the same
fixtures work with whatever real names are configured.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from backend import templates
from backend.models.base import Correction, InstanceDesc, TimedFrame, Verification
from backend.schemas import EventFacts, Language, ParsedRule, Rule

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "mock" / "spark_parse_rule.json"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_KANNADA = re.compile(r"[ಀ-೿]")
_HINGLISH = re.compile(r"\b(batana|batao|raat|agar|toh|mujhe|papa ko|dekhna)\b", re.IGNORECASE)
_NUMBER_WORDS = {"दस": 10, "पांच": 5, "पाँच": 5, "बीस": 20, "ten": 10, "five": 5, "twenty": 20,
                 "ಹತ್ತು": 10, "ಐದು": 5, "ಇಪ್ಪತ್ತು": 20}
_NIGHT = re.compile(r"night|raat|रात|ರಾತ್ರಿ", re.IGNORECASE)
_MINUTES = re.compile(r"min|मिनट|ನಿಮಿಷ", re.IGNORECASE)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text).casefold()
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def detect_language(text: str) -> Language:
    if _KANNADA.search(text):
        return "kn"
    if _DEVANAGARI.search(text) or _HINGLISH.search(text):
        return "hi"
    return "en"


def _first_number(text: str) -> int | None:
    if m := re.search(r"\d+", text):
        return int(m.group())
    for word, n in _NUMBER_WORDS.items():
        if word in text.casefold():
            return n
    return None


def _has(text: str, *words: str) -> bool:
    t = text.casefold()
    return any(w in t for w in words)


class MockSpark:
    def __init__(self, fixtures: Path = FIXTURES) -> None:
        entries = json.loads(fixtures.read_text(encoding="utf-8")) if fixtures.exists() else []
        self._fixtures = {normalize(e["text"]): e["response"] for e in entries}

    async def parse_rule(self, text: str, members: list[str], sender: str) -> ParsedRule:
        raw = self._fixtures.get(normalize(text)) or self._heuristic(text)
        raw = dict(raw)
        raw["notify"] = [self._fill(n, members) for n in raw["notify"]]
        return ParsedRule.model_validate(raw)

    @staticmethod
    def _fill(name: str, members: list[str]) -> str:
        if m := re.fullmatch(r"\{MEMBER:(\d+)\}", name):
            i = int(m.group(1)) - 1
            return members[i] if 0 <= i < len(members) else name
        return name

    @staticmethod
    def _heuristic(text: str) -> dict:
        lang = detect_language(text)
        base = {"status": "ok", "clarifying_question": None, "subject": "person", "subject_name": None,
                "zone_object": None, "condition": "none", "duration_sec": 0, "time_window": None,
                "guard": None, "notify": ["me"], "language": lang}
        n = _first_number(text)
        duration = n * 60 if n and _MINUTES.search(text) else (n or 0)
        if _NIGHT.search(text):
            base["time_window"] = {"start": "22:00", "end": "06:00"}
        sofa = _has(text, "sofa", "सोफ़", "सोफ", "ಸೋಫಾ")
        if sofa and _has(text, "dog", "कुत्त", "ನಾಯಿ"):
            base.update(subject="dog", zone_object="sofa", condition="ON_OBJECT")
        elif _has(text, "bed", "बिस्तर", "ಹಾಸಿಗೆ", "papa", "पापा", "ಅಪ್ಪ", "sofa", "सोफ़", "सोफ", "ಸೋಫಾ") and _has(
                text, "bed", "बिस्तर", "ಹಾಸಿಗೆ", "get up", "gets up", "leave", "उठ", "छोड़", "ಎದ್ದ"):
            base.update(zone_object="sofa" if sofa else "bed", condition="ABSENT_FROM_ZONE", duration_sec=duration)
        elif _has(text, "stove", "चूल्हा", "गैस", "ಸ್ಟೌ", "ಒಲೆ"):
            base.update(zone_object="gas stove", condition="NO_PERSON_NEAR", duration_sec=duration,
                        guard={"subject": "flame", "zone_object": "gas stove"})
        elif _has(text, "door", "दरवाज", "ಬಾಗಿಲ"):
            base.update(zone_object="main door", condition="PRESENT_IN_ZONE")
        else:
            base.update(status="needs_clarification", subject="none")
        for word in ("papa", "पापा", "ಅಪ್ಪ", "appa"):
            if word in text.casefold() and base["subject"] == "person":
                base["subject_name"] = next(w for w in text.split() if word in w.casefold())
                break
        return base

    async def restate(self, rule: Rule, zone_count: int, names: list[str]) -> str:
        return templates.restatement(rule, zone_count, names)

    async def resolve_correction(
        self, snapshot: bytes, instances: list[InstanceDesc], reply: str, current: int | None
    ) -> Correction:
        if m := re.search(r"\d+", reply):
            return Correction("select_instance", instance=int(m.group()))
        if _has(reply, "cancel", "रद्द", "रहने दो", "ಬೇಡ"):
            return Correction("cancel")
        if _has(reply, "other", "दूसरा", "दूसरे", "ಇನ್ನೊಂದು", "ಬೇರೆ") and len(instances) == 2:
            return Correction("select_instance", instance=1 if current == 2 else 2)
        return Correction("reparse", new_text=reply)

    async def confirm_event(self, frames: list[TimedFrame], claim: str) -> Verification:
        return Verification(consistent=True, visible_summary="(mock) frames match the claim", confidence="high")

    async def write_alert(self, facts: EventFacts) -> str:
        return templates.alert(facts)
