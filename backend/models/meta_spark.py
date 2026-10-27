"""Muse Spark through Chat Completions with schema-constrained JSON (PLAN.md §3.2, §5; test calls 2-3).

Restatements and alert text come from the templates until the Spark writer and its validator land
(PLAN.md §8, Fri 30 Oct); the family only ever reads text built from the validated rule JSON.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

from backend import templates
from backend.budget import Budget, Usage
from backend.clock import local_hhmm
from backend.config import Settings
from backend.models.base import Correction, InstanceDesc, TimedFrame, Verification
from backend.models.meta_common import chat_usage, make_client, with_retries
from backend.recorder import Recorder
from backend.schemas import RULE_LLM_SCHEMA, EventFacts, ParsedRule, Rule

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["consistent", "visible_summary", "confidence"],
    "properties": {"consistent": {"type": "boolean"}, "visible_summary": {"type": "string"},
                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]}},
}
CORRECTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["action", "instance", "new_text"],
    "properties": {"action": {"type": "string", "enum": ["select_instance", "reparse", "cancel"]},
                   "instance": {"type": ["integer", "null"]}, "new_text": {"type": ["string", "null"]}},
}

# Budget estimates before the call (actual usage is recorded after). Test call 2: ~1.7k output
# tokens at "low"; call 3: ~400 input tokens per 640 px frame, ~250 output tokens at "minimal".
EST_PARSE = Usage(input_tokens=2500, output_tokens=2500)
EST_PER_FRAME = 450
EST_MINIMAL_OUT = 600


def _user_block(household: list[str], sender: str, message: str) -> str:
    return f"HOUSEHOLD: {', '.join(household)}\nSENDER: {sender}\nMESSAGE: {message}"


def _jpeg_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


class MetaSpark:
    def __init__(self, settings: Settings, budget: Budget, *, recorder: Recorder | None = None,
                 http_client: httpx.AsyncClient | None = None) -> None:
        self.model = settings.spark_model
        self.budget = budget
        self.recorder = recorder
        self.client = make_client(settings, http_client)
        self.parse_system = (PROMPTS / "parse_rule.md").read_text(encoding="utf-8")
        self.parse_examples = json.loads((PROMPTS / "parse_rule_examples.json").read_text(encoding="utf-8"))
        self.verify_system = (PROMPTS / "confirm_event.md").read_text(encoding="utf-8")
        self.correction_system = (PROMPTS / "resolve_correction.md").read_text(encoding="utf-8")
        self.last_latency_ms: int | None = None

    async def _json(self, purpose: str, messages: list[dict], name: str, schema: dict, effort: str,
                    estimate: Usage, timeout: float) -> dict:
        async def attempt() -> dict:
            async with self.budget.guard(self.model, purpose, estimate) as call:
                t0 = time.perf_counter()
                r = await self.client.chat.completions.create(
                    model=self.model, messages=messages, reasoning_effort=effort, timeout=timeout,
                    response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
                )
                call.usage = chat_usage(r.usage)
                self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
                return json.loads(r.choices[0].message.content)
        return await with_retries(attempt)

    async def parse_rule(self, text: str, members: list[str], sender: str) -> ParsedRule:
        messages: list[dict] = [{"role": "system", "content": self.parse_system}]
        for ex in self.parse_examples:
            messages.append({"role": "user", "content": _user_block(ex["household"], ex["sender"], ex["message"])})
            messages.append({"role": "assistant", "content": json.dumps(ex["rule"], ensure_ascii=False)})
        messages.append({"role": "user", "content": _user_block(members, sender, text)})
        raw = await self._json("parse_rule", messages, "kinwatch_rule", RULE_LLM_SCHEMA, "low", EST_PARSE, 45.0)
        if self.recorder:
            self.recorder.spark_parse(text, raw)
        return ParsedRule.model_validate(raw)

    async def restate(self, rule: Rule, zone_count: int, names: list[str]) -> str:
        return templates.restatement(rule, zone_count, names)

    async def resolve_correction(
        self, snapshot: bytes, instances: list[InstanceDesc], reply: str, current: int | None
    ) -> Correction:
        listing = "; ".join(f"{d.n}: {d.position}, size rank {d.size_rank}" for d in instances)
        content = [{"type": "text", "text": f"Numbered objects: {listing}. Currently selected: {current}. "
                                            f"Their reply: \"{reply}\""}, _jpeg_part(snapshot)]
        estimate = Usage(input_tokens=600 + EST_PER_FRAME, output_tokens=EST_MINIMAL_OUT)
        raw = await self._json("resolve_correction", [{"role": "system", "content": self.correction_system},
                                                      {"role": "user", "content": content}],
                               "correction", CORRECTION_SCHEMA, "minimal", estimate, 25.0)
        return Correction(action=raw["action"], instance=raw.get("instance"), new_text=raw.get("new_text"))

    async def confirm_event(self, frames: list[TimedFrame], statements: list[str]) -> Verification:
        lines = [f"Frame {i + 1} ({local_hhmm(f.ts)}): {s}" for i, (f, s) in enumerate(zip(frames, statements))]
        content: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
        content += [_jpeg_part(f.jpeg) for f in frames]
        estimate = Usage(input_tokens=400 + EST_PER_FRAME * len(frames), output_tokens=EST_MINIMAL_OUT)
        raw = await self._json("confirm_event", [{"role": "system", "content": self.verify_system},
                                                 {"role": "user", "content": content}],
                               "event_check", VERIFY_SCHEMA, "minimal", estimate, 25.0)
        return Verification(consistent=bool(raw["consistent"]), visible_summary=str(raw["visible_summary"])[:200],
                            confidence=raw["confidence"])

    async def write_alert(self, facts: EventFacts) -> str:
        return templates.alert(facts)
