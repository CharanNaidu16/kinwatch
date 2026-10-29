"""The watching loop: frames -> SAM (only when needed) -> geometry -> each rule's engine (PLAN.md §3.4-3.5).

Cost control: SAM is called only for subjects of armed rules that are inside their time window,
only when the picture changed (or a heartbeat is due), at most one call in flight per subject, and
never more often than SAM_MIN_INTERVAL_SEC. Anything that prevents seeing (dark frame, SAM error,
budget refusal, camera moved) is an UNKNOWN reading, never a guess.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from backend.clock import Clock, local_hhmm
from backend.config import Settings
from backend.engine import Phase, Reading, RuleEngine, Transition
from backend.models import Adapters
from backend.models.base import Instance
from backend.schemas import Condition, Observation, Rule, Subject
from backend.storage import Storage
from backend.vision import MIN_CONFIDENCE, ChangeDetector, Frame, FrameBuffer, Zone, is_in, is_near, is_on
from backend.zones import load_candidates

log = logging.getLogger("kinwatch.watcher")

SUBJECT_CONCEPTS = {Subject.PERSON: "person", Subject.DOG: "dog", Subject.CAT: "cat", Subject.FLAME: "flame"}
GUARD_STALE_SEC = 90  # a guard reading older than this counts as unknown
CAMERA_LOST_SEC = 120  # no frame for this long while a rule is watching: tell the family once

EventHandler = Callable[[Rule, Transition, list[Frame], Zone, bool], Awaitable[None]]
CameraHandler = Callable[[str, float], Awaitable[None]]  # ("lost" | "back", since)


@dataclass
class Runtime:
    rule: Rule
    zone: Zone | None
    engine: RuleEngine
    guard_value: bool | None = None
    guard_ts: float | None = None
    last_held_false: Frame | None = None  # last frame where the condition did NOT hold (evidence)
    pending_frame: Frame | None = None
    last_count: int | None = None  # subjects in/on/near the zone at the last analysis (behind-the-scenes page)

    @property
    def subject_concept(self) -> str:
        return SUBJECT_CONCEPTS[self.rule.subject]

    @property
    def guard_concept(self) -> str | None:
        return SUBJECT_CONCEPTS[self.rule.guard.subject] if self.rule.guard else None


@dataclass
class Watcher:
    storage: Storage
    settings: Settings
    clock: Clock
    adapters: Adapters
    frames: FrameBuffer
    household_id: int
    on_event: EventHandler | None = None
    on_camera: CameraHandler | None = None
    runtimes: dict[int, Runtime] = field(default_factory=dict)
    _last_call: dict[str, float] = field(default_factory=dict)
    _inflight: set[str] = field(default_factory=set)
    _detectors: dict[str, ChangeDetector] = field(default_factory=dict)
    _tasks: set[asyncio.Task] = field(default_factory=set)
    _started: float | None = None
    last_seen: dict[str, tuple[float, list[Instance] | None]] = field(default_factory=dict)  # concept -> latest SAM result
    _camera_lost: bool = False

    # --- rules ------------------------------------------------------------------------------------

    def reload(self) -> None:
        """(Re)load armed rules. Engines of rules that stay armed keep their state."""
        rows = self.storage.query("SELECT id FROM rules WHERE household_id = ? AND status = 'armed'",
                                  (self.household_id,))
        armed = {r["id"] for r in rows}
        for rule_id in list(self.runtimes):
            if rule_id not in armed:
                del self.runtimes[rule_id]
        for rule_id in armed - set(self.runtimes):
            rule = self.storage.get_rule(rule_id)
            if rule is None:
                continue
            self.runtimes[rule_id] = Runtime(rule, self._zone(rule), self._engine(rule))

    def _zone(self, rule: Rule) -> Zone | None:
        masks = load_candidates(self.settings.data_path, rule.id)
        index = (rule.zone_instance or 1) - 1
        if not 0 <= index < len(masks):
            log.warning("rule %s has no stored zone; it will read UNKNOWN", rule.id)
            return None
        return Zone.from_mask(masks[index], self.settings.sam_concept(rule.zone_object))

    def _engine(self, rule: Rule) -> RuleEngine:
        row = self.storage.query_one("SELECT state_json FROM rule_state WHERE rule_id = ?", (rule.id,))
        if row:  # resume after a restart: a PENDING timer keeps its start time
            return RuleEngine.from_dict(json.loads(row["state_json"]))
        scale = self.settings.effective_time_scale
        return RuleEngine(condition=rule.condition, duration_sec=rule.duration_sec / scale, window=rule.time_window,
                          has_guard=rule.guard is not None, cooldown_sec=600 / scale)

    def _save(self, rt: Runtime) -> None:
        self.storage.execute(
            "INSERT INTO rule_state (rule_id, state_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(rule_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
            (rt.rule.id, json.dumps(rt.engine.to_dict()), self.clock.now()))

    # --- scheduling -------------------------------------------------------------------------------

    def _concepts(self) -> dict[str, list[Runtime]]:
        out: dict[str, list[Runtime]] = {}
        for rt in self.runtimes.values():
            if rt.engine.phase == Phase.OUT_OF_WINDOW or rt.zone is None:
                continue  # no zone, nothing to compare against: don't pay for SAM calls
            out.setdefault(rt.subject_concept, []).append(rt)
            if rt.guard_concept:
                out.setdefault(rt.guard_concept, []).append(rt)
        return out

    def _due(self, concept: str, users: list[Runtime], frame: Frame, now: float) -> bool:
        if concept in self._inflight:
            return False
        last = self._last_call.get(concept)
        if last is not None and now - last < self.settings.sam_min_interval_sec:
            return False
        if any(rt.engine.settling for rt in users):
            return True  # confirm a fresh change quickly (still no closer than the minimum interval)
        busy = any(rt.engine.phase in (Phase.PENDING, Phase.WAITING_BASELINE) for rt in users)
        heartbeat = self.settings.sam_pending_heartbeat_sec if busy else self.settings.sam_heartbeat_sec
        if last is None or now - last >= heartbeat:
            return True
        return self._detectors.setdefault(concept, ChangeDetector()).changed(frame)

    async def on_frame(self, frame: Frame) -> list[asyncio.Task]:
        """Called for every uploaded frame. Returns the analysis tasks it started (tests await them)."""
        now = self.clock.now()
        await self.tick(now, frame)
        started = []
        for concept, users in self._concepts().items():
            if self._due(concept, users, frame, now):
                self._inflight.add(concept)
                self._last_call[concept] = now
                task = asyncio.create_task(self.analyse(concept, frame))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                started.append(task)
        return started

    async def tick(self, now: float, frame: Frame | None = None) -> None:
        for rt in list(self.runtimes.values()):
            await self._handle(rt, rt.engine.tick(now), frame)
        await self._check_camera(now)

    async def _check_camera(self, now: float) -> None:
        """A camera that silently stops looks exactly like "all quiet", so say so (PLAN.md §6.3)."""
        if self._started is None:
            self._started = now
        latest = self.frames.latest()
        last = latest.ts if latest is not None else self._started
        watching = any(rt.zone is not None and rt.engine.phase != Phase.OUT_OF_WINDOW for rt in self.runtimes.values())
        if not self._camera_lost and watching and now - last > CAMERA_LOST_SEC:
            self._camera_lost = True
            self.storage.add_timeline(self.household_id, now, "camera_lost",
                                      f"{local_hhmm(now)} no pictures from the camera since {local_hhmm(last)}")
            if self.on_camera is not None:
                await self.on_camera("lost", last)
        elif self._camera_lost and latest is not None and now - latest.ts <= CAMERA_LOST_SEC / 4:
            self._camera_lost = False
            self.storage.add_timeline(self.household_id, now, "camera_back", f"{local_hhmm(now)} camera is back")
            if self.on_camera is not None:
                await self.on_camera("back", now)

    # --- analysis ---------------------------------------------------------------------------------

    async def analyse(self, concept: str, frame: Frame) -> None:
        self._inflight.add(concept)
        try:
            self._detectors.setdefault(concept, ChangeDetector()).mark_analysed(frame)
            instances = await self._segment(concept, frame)
            self.last_seen[concept] = (frame.ts, instances)
            for rt in list(self.runtimes.values()):
                if rt.engine.phase == Phase.OUT_OF_WINDOW:
                    continue
                if concept == rt.guard_concept and concept != rt.subject_concept:
                    rt.guard_value = None if instances is None else self._guard_holds(rt, instances, frame)
                    rt.guard_ts = frame.ts
                if concept == rt.subject_concept:
                    reading = self._reading(rt, instances, frame)
                    rt.last_count = reading.count
                    transitions = rt.engine.observe(frame.ts, reading)
                    if rt.engine.observation(reading) == Observation.FALSE:
                        rt.last_held_false = frame
                    await self._handle(rt, transitions, frame)
        finally:
            self._inflight.discard(concept)

    async def _segment(self, concept: str, frame: Frame) -> list[Instance] | None:
        if frame.dark:
            return None
        try:
            found = await self.adapters.segmenter.segment(frame.jpeg, concept, purpose="watch")
        except Exception as exc:  # budget refusal, API error, timeout: all mean "couldn't see"
            log.warning("SAM '%s' failed: %s", concept, exc)
            return None
        return [i for i in found if i.confidence is None or i.confidence >= MIN_CONFIDENCE]

    def _reading(self, rt: Runtime, instances: list[Instance] | None, frame: Frame) -> Reading:
        zone = rt.zone
        if instances is None or zone is None or zone.shape != (frame.height, frame.width):
            return Reading(None)
        c = rt.rule.condition
        if c == Condition.NO_PERSON_NEAR or (c == Condition.PRESENT_IN_ZONE and zone.door):
            count = sum(is_near((i.box.x1, i.box.y1, i.box.x2, i.box.y2), zone) for i in instances)
        elif c == Condition.ON_OBJECT:
            count = sum(is_on(i.mask, zone) for i in instances)
        else:
            count = sum(is_in(i.mask, zone) for i in instances)
        guard = None
        if rt.rule.guard is not None and rt.guard_ts is not None and frame.ts - rt.guard_ts <= GUARD_STALE_SEC:
            guard = rt.guard_value
        return Reading(count=count, guard=guard)

    @staticmethod
    def _guard_holds(rt: Runtime, instances: list[Instance], frame: Frame) -> bool:
        zone = rt.zone
        if zone is None or zone.shape != (frame.height, frame.width):
            return False
        return any(is_in(i.mask, zone) or is_near((i.box.x1, i.box.y1, i.box.x2, i.box.y2), zone) for i in instances)

    # --- transitions ------------------------------------------------------------------------------

    async def _handle(self, rt: Runtime, transitions: list[Transition], frame: Frame | None) -> None:
        if not transitions:
            return
        self._save(rt)
        rule = rt.rule
        for t in transitions:
            text = _describe(rule, t)
            self.storage.add_timeline(self.household_id, t.ts, f"rule_{t.kind}", text, rule_id=rule.id, data=t.data)
            if t.kind == "pending":
                rt.pending_frame = frame
            if t.kind == "fired" and self.on_event is not None and rt.zone is not None:
                latest = self.frames.latest() or frame
                evidence = [f for f in (rt.last_held_false, rt.pending_frame, latest) if f is not None]
                # Without a frame from before the condition started, every frame shows it holding.
                has_before = rt.last_held_false is not None
                try:
                    await self.on_event(rule, t, evidence, rt.zone, has_before)
                except Exception:
                    log.exception("alert for rule %s failed", rule.id)


def _describe(rule: Rule, t: Transition) -> str:
    who = rule.subject_name or rule.subject.value
    zone = rule.zone_object
    at = local_hhmm(t.ts)
    if t.kind == "window_open":
        return f"{at} rule {rule.id}: watching window opened"
    if t.kind == "window_closed":
        return f"{at} rule {rule.id}: window closed (was {t.data.get('was', '').lower()})"
    if t.kind == "baseline":
        return f"{at} {t.data.get('count')} {rule.subject.value} in the {zone}: baseline set"
    if t.kind == "pending":
        return f"{local_hhmm(t.data.get('since', t.ts))} {who}: condition started ({rule.condition.value})"
    if t.kind == "returned":
        return f"{at} {who}: back after {int(t.data.get('after_sec', 0))} s, timer reset"
    if t.kind == "fired":
        return f"{at} {who}: rule met after {int(rule.duration_sec)} s, alerting"
    return f"{at} rule {rule.id}: occurrence over"
