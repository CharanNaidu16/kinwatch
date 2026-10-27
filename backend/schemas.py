"""Core data types shared across modules (PLAN.md §4)."""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HHMM_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
_HHMM = re.compile(HHMM_PATTERN)


class Condition(str, Enum):
    PRESENT_IN_ZONE = "PRESENT_IN_ZONE"
    ABSENT_FROM_ZONE = "ABSENT_FROM_ZONE"
    ON_OBJECT = "ON_OBJECT"
    NO_PERSON_NEAR = "NO_PERSON_NEAR"


class Subject(str, Enum):
    PERSON = "person"
    DOG = "dog"
    CAT = "cat"
    FLAME = "flame"


class RuleStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    ARMED = "armed"
    PAUSED = "paused"
    DELETED = "deleted"


class Observation(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


Language = Literal["hi", "kn", "en"]
LANGUAGES: tuple[Language, ...] = ("hi", "kn", "en")


class TimeWindow(BaseModel):
    """Daily window in IST. start > end means it crosses midnight (22:00-06:00)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError(f"expected HH:MM, got {v!r}")
        return v

    @staticmethod
    def _minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    @property
    def start_min(self) -> int:
        return self._minutes(self.start)

    @property
    def end_min(self) -> int:
        return self._minutes(self.end)


class Guard(BaseModel):
    """An extra fact that must also hold, e.g. a flame on the gas stove (NO_PERSON_NEAR only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: Subject
    zone_object: str


# --- What Muse Spark returns -------------------------------------------------------------------

# Sent as response_format json_schema. Accepted as-is by muse-spark-1.3 (test call 2, 26 Oct):
# nullable unions, pattern, enum, min/max all work. Keep it in sync with ParsedRule below.
RULE_LLM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "clarifying_question", "subject", "subject_name", "zone_object",
                 "condition", "duration_sec", "time_window", "guard", "notify", "language"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "needs_clarification", "unsupported"]},
        "clarifying_question": {"type": ["string", "null"]},
        "subject": {"type": "string", "enum": [s.value for s in Subject] + ["none"]},
        "subject_name": {"type": ["string", "null"]},
        "zone_object": {"type": ["string", "null"]},
        "condition": {"type": "string", "enum": [c.value for c in Condition] + ["none"]},
        "duration_sec": {"type": "integer", "minimum": 0, "maximum": 43200},
        "time_window": {
            "type": ["object", "null"], "additionalProperties": False, "required": ["start", "end"],
            "properties": {"start": {"type": "string", "pattern": HHMM_PATTERN},
                           "end": {"type": "string", "pattern": HHMM_PATTERN}},
        },
        "guard": {
            "type": ["object", "null"], "additionalProperties": False, "required": ["subject", "zone_object"],
            "properties": {"subject": {"type": "string", "enum": [s.value for s in Subject]},
                           "zone_object": {"type": "string"}},
        },
        "notify": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
        "language": {"type": "string", "enum": list(LANGUAGES)},
    },
}


class ParsedRule(BaseModel):
    """Muse Spark's rule JSON, before Kinwatch's own validation (rules.validate_parsed)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "needs_clarification", "unsupported"]
    clarifying_question: str | None
    subject: Subject | Literal["none"]
    subject_name: str | None
    zone_object: str | None
    condition: Condition | Literal["none"]
    duration_sec: int = Field(ge=0, le=43200)
    time_window: TimeWindow | None
    guard: Guard | None
    notify: list[str] = Field(min_length=1, max_length=5)
    language: Language


# --- Household ---------------------------------------------------------------------------------

class Member(BaseModel):
    id: int
    household_id: int
    name: str
    channel: Literal["whatsapp", "telegram", "mock"] = "whatsapp"
    address: str  # never logged or committed
    aliases: list[str] = Field(default_factory=list)
    escalation_order: int = 0
    is_admin: bool = False
    last_inbound_at: float | None = None

    def __repr__(self) -> str:  # keep phone numbers out of logs and tracebacks
        return f"Member(id={self.id}, name={self.name!r})"

    __str__ = __repr__


# --- A validated, stored rule ------------------------------------------------------------------

class Rule(BaseModel):
    id: int | None = None
    household_id: int
    created_by: int  # member id
    original_text: str
    input_kind: Literal["text", "voice"] = "text"
    transcript: str | None = None

    subject: Subject
    subject_name: str | None = None
    zone_object: str
    condition: Condition
    duration_sec: int = Field(ge=0, le=43200)
    time_window: TimeWindow | None = None
    guard: Guard | None = None
    notify: list[int] = Field(min_length=1)  # member ids, in order
    language: Language

    status: RuleStatus = RuleStatus.DRAFT
    zone_instance: int | None = None
    restatement: str | None = None
    created_at: float | None = None
    armed_at: float | None = None
    schema_version: int = 1

    def rule_json(self) -> dict:
        """The part of the rule shown next to the sentence on the behind-the-scenes page."""
        return self.model_dump(
            mode="json",
            include={"subject", "subject_name", "zone_object", "condition", "duration_sec",
                     "time_window", "guard", "notify", "language"},
        )


# --- Events ------------------------------------------------------------------------------------

class EventFacts(BaseModel):
    """Structured facts handed to verification and alert writing. Only what was seen, and when."""

    rule_id: int
    subject_name: str | None
    subject: Subject
    zone_object: str
    condition: Condition
    what: str
    started_at_local: str
    met_at_local: str
    duration_min: int
    visibility: Literal["good", "poor"] = "good"
    check_in: str | None = None
    language: Language
