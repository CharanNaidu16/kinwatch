"""Adapter protocols and the data they exchange. Real and mock adapters implement the same protocols."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from backend.schemas import EventFacts, Language, ParsedRule, Rule, Subject


@dataclass(frozen=True)
class Box:
    """Pixel box, inclusive corners, in the coordinates of the frame that was segmented."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1 + 1

    @property
    def height(self) -> int:
        return self.y2 - self.y1 + 1


@dataclass
class Instance:
    box: Box
    mask: np.ndarray  # bool, shape (H, W) of the segmented frame
    confidence: float | None = None


@dataclass(frozen=True)
class Transcript:
    text: str
    duration_ms: int


@dataclass(frozen=True)
class InstanceDesc:
    """What resolve_correction knows about each numbered outline in the snapshot."""

    n: int
    position: str  # e.g. "left", "right, near the top"
    size_rank: int  # 1 = largest


@dataclass(frozen=True)
class Correction:
    action: Literal["select_instance", "reparse", "cancel"]
    instance: int | None = None
    new_text: str | None = None


@dataclass(frozen=True)
class TimedFrame:
    ts: float
    jpeg: bytes


@dataclass(frozen=True)
class Question:
    """What a family member asked the camera about right now (Muse Spark, PLAN.md "ask the camera")."""

    kind: Literal["question", "rule", "unsupported"]
    subject: Subject
    subject_name: str | None
    zone_object: str | None
    relation: Literal["in", "on", "near", "visible"]
    language: Language


@dataclass(frozen=True)
class Verification:
    consistent: bool
    visible_summary: str
    confidence: Literal["low", "medium", "high"]


class Transcriber(Protocol):
    async def transcribe(self, wav: bytes, language_bias: list[str]) -> Transcript: ...


class Segmenter(Protocol):
    async def segment(self, jpeg: bytes, concept: str, *, purpose: str = "segment_subject") -> list[Instance]: ...


class Spark(Protocol):
    async def parse_rule(self, text: str, members: list[str], sender: str) -> ParsedRule: ...

    async def restate(self, rule: Rule, zone_count: int, names: list[str]) -> str: ...

    async def resolve_correction(
        self, snapshot: bytes, instances: list[InstanceDesc], reply: str, current: int | None
    ) -> Correction: ...

    async def confirm_event(self, frames: list[TimedFrame], statements: list[str]) -> Verification:
        """One factual statement per frame (PLAN.md §3.6); advisory only, never suppresses an alert."""
        ...

    async def write_alert(self, facts: EventFacts) -> str: ...

    async def parse_question(self, text: str) -> Question: ...
