"""Rule validation: Muse Spark's JSON is checked here before anything is shown to the family.

Constrained decoding guarantees the JSON's shape, not its meaning (test call 2 added a spurious
guard), so every semantic rule from PLAN.md §3.2 is enforced in code.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from backend.schemas import (
    Condition,
    Guard,
    Language,
    Member,
    ParsedRule,
    Rule,
    RuleStatus,
    Subject,
)
from backend.texts import text

MAX_ZONE_WORDS = 4
DEFAULT_DURATION_SEC = {Condition.ABSENT_FROM_ZONE: 300, Condition.NO_PERSON_NEAR: 300}

# Words that turn a request into something about health. Kinwatch never accepts those rules,
# whatever the model says. English entries are matched on word boundaries; others as substrings.
_HEALTH_TERMS_EN = [
    "health", "healthy", "sick", "ill", "illness", "unwell", "medicine", "medicines", "medication",
    "pills", "blood pressure", "bp", "sugar level", "diabetes", "heart", "breathing", "breath",
    "fever", "pain", "fall down", "falls down", "fell", "fallen", "has a fall", "faint", "fainted",
    "unconscious", "injury", "injured", "hurt", "stroke", "seizure", "dizzy", "mood", "depressed",
    "tabiyat", "tabiyet", "bimar", "beemar", "dawa", "dawai", "bukhar", "dard", "behosh", "saans",
    "gir gaye", "gir jaye", "gir jaaye",
]
_HEALTH_TERMS_SCRIPT = [
    # Hindi
    "तबीयत", "तबियत", "बीमार", "सेहत", "दवा", "दवाई", "बुखार", "दर्द", "गिर जा", "गिर ग", "गिरे",
    "सांस", "साँस", "बेहोश", "चक्कर", "शुगर",
    # Kannada
    "ಆರೋಗ್ಯ", "ಔಷಧ", "ಮಾತ್ರೆ", "ಜ್ವರ", "ನೋವು", "ಬಿದ್ದ", "ಉಸಿರು", "ಹುಷಾರಿಲ್ಲ", "ತಲೆ ಸುತ್ತು",
]
_HEALTH_EN_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _HEALTH_TERMS_EN) + r")\b", re.IGNORECASE)

_SELF_TOKENS = {"me", "myself", "sender", "mujhe", "mujhko", "mere ko", "मुझे", "मुझको", "nanage", "ನನಗೆ"}
_ALL_TOKENS = {"everyone", "everybody", "all", "family", "the family", "us", "sabko", "sab",
               "सबको", "सब", "परिवार", "ellarigu", "ಎಲ್ಲರಿಗೂ"}
_LIST_SEPARATORS = re.compile(r"[,;+&]|\band\b|\baur\b|\bmattu\b|और|ಮತ್ತು", re.IGNORECASE)


@dataclass
class Validated:
    kind: Literal["ok", "clarify", "unsupported"]
    rule: Rule | None = None
    question: str | None = None
    notes: list[str] = field(default_factory=list)  # silent fixes, shown on the behind-the-scenes page


def mentions_health(message: str) -> bool:
    if _HEALTH_EN_RE.search(message):
        return True
    normalized = unicodedata.normalize("NFC", message)
    return any(term in normalized for term in _HEALTH_TERMS_SCRIPT)


def normalize_object(phrase: str) -> str:
    return " ".join(phrase.strip().strip(".!?\"'").lower().split())


def _name_key(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def resolve_notify(names: list[str], members: list[Member], sender: Member) -> tuple[list[int], str | None]:
    """Map the model's notify names to member ids, in order. Returns (ids, first unknown name)."""
    by_key: dict[str, Member] = {}
    for m in members:
        for alias in [m.name, *m.aliases]:
            by_key.setdefault(_name_key(alias), m)
    ids: list[int] = []

    def add(member_id: int) -> None:
        if member_id not in ids:
            ids.append(member_id)

    for raw in names:
        token = " ".join(raw.strip().casefold().split())
        if token in _SELF_TOKENS:
            add(sender.id)
        elif token in _ALL_TOKENS:
            for m in sorted(members, key=lambda m: m.escalation_order):
                add(m.id)
        elif (m := by_key.get(_name_key(raw))) is not None:
            add(m.id)
        else:
            return ids, raw.strip()
    if not ids:
        ids.append(sender.id)
    return ids, None


def validate_parsed(
    parsed: ParsedRule,
    *,
    original_text: str,
    members: list[Member],
    sender: Member,
    household_id: int,
    input_kind: Literal["text", "voice"] = "text",
    transcript: str | None = None,
) -> Validated:
    lang: Language = parsed.language

    if mentions_health(original_text):
        question = parsed.clarifying_question if parsed.status == "unsupported" and parsed.clarifying_question else None
        return Validated("unsupported", question=question or text("unsupported", lang),
                         notes=["health terms in the message"])
    if parsed.status == "unsupported":
        return Validated("unsupported", question=parsed.clarifying_question or text("unsupported", lang))
    if parsed.status == "needs_clarification":
        return Validated("clarify", question=parsed.clarifying_question or text("clarify_generic", lang))

    notes: list[str] = []
    if parsed.subject == "none" or parsed.condition == "none" or not (parsed.zone_object or "").strip():
        return Validated("clarify", question=text("clarify_generic", lang),
                         notes=["status ok but subject, condition or zone missing"])

    zone = normalize_object(parsed.zone_object or "")
    if _LIST_SEPARATORS.search(zone):
        parts = [p.strip() for p in _LIST_SEPARATORS.split(zone) if p and p.strip()]
        return Validated("clarify", question=text("clarify_one_object", lang, objects=" / ".join(parts)),
                         notes=["zone_object is a list; SAM takes one concept per request"])
    if len(zone.split()) > MAX_ZONE_WORDS:
        return Validated("clarify", question=text("clarify_generic", lang),
                         notes=[f"zone_object longer than {MAX_ZONE_WORDS} words"])

    subject = Subject(parsed.subject)
    condition = Condition(parsed.condition)
    guard = parsed.guard

    if condition == Condition.NO_PERSON_NEAR:
        # "Nobody near" is always about people. If the model put the other fact in subject
        # ("flame ... nobody near the stove"), move it into the guard.
        if subject != Subject.PERSON:
            if guard is None:
                guard = Guard(subject=subject, zone_object=zone)
                notes.append(f"moved subject {subject.value!r} into guard")
            subject = Subject.PERSON
        if guard is not None and guard.subject == Subject.PERSON:
            guard = None
            notes.append("dropped guard on person (contradicts NO_PERSON_NEAR)")
    elif guard is not None:
        guard = None
        notes.append(f"dropped guard (only allowed with NO_PERSON_NEAR, condition is {condition.value})")
    if guard is not None:
        guard_zone = normalize_object(guard.zone_object) or zone
        if _LIST_SEPARATORS.search(guard_zone):
            guard_zone = zone
            notes.append("guard zone was a list; using the rule's zone")
        guard = Guard(subject=guard.subject, zone_object=guard_zone)

    duration = parsed.duration_sec
    if duration == 0 and condition in DEFAULT_DURATION_SEC:
        duration = DEFAULT_DURATION_SEC[condition]
        notes.append(f"default duration {duration}s")

    window = parsed.time_window
    if window is not None and window.start == window.end:
        return Validated("clarify", question=text("clarify_time_window", lang),
                         notes=["time window start equals end"])

    notify, unknown = resolve_notify(parsed.notify, members, sender)
    if unknown is not None:
        known = ", ".join(m.name for m in sorted(members, key=lambda m: m.escalation_order))
        return Validated("clarify", question=text("clarify_unknown_name", lang, name=unknown, known=known),
                         notes=[f"unknown notify name {unknown!r}"])

    subject_name = (parsed.subject_name or "").strip() or None
    rule = Rule(
        household_id=household_id,
        created_by=sender.id,
        original_text=original_text,
        input_kind=input_kind,
        transcript=transcript,
        subject=subject,
        subject_name=subject_name,
        zone_object=zone,
        condition=condition,
        duration_sec=duration,
        time_window=window,
        guard=guard,
        notify=notify,
        language=lang,
        status=RuleStatus.DRAFT,
    )
    return Validated("ok", rule=rule, notes=notes)
