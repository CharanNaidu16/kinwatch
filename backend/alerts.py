"""Events, alerts and claims (PLAN.md §3.6, §3.8).

When a rule fires: record the event, write the alert (templates; Spark writer from Fri 30 Oct), send
the photo with an "I've got it" button to everyone on the rule, and check the frames with Muse
Spark in the background. The check is advisory only and never holds an alert back (test call 3).
Claims are atomic: SQLite serialises writers, so exactly one of two simultaneous taps wins.
Escalation to the next member arrives on Fri 30 Oct.
"""
from __future__ import annotations

import asyncio
import json
import logging

from backend import templates
from backend.clock import Clock, local_hhmm
from backend.config import Settings
from backend.engine import Transition
from backend.messaging.base import Button
from backend.models import Adapters
from backend.models.base import TimedFrame
from backend.schemas import Condition, EventFacts, Member, Rule
from backend.snapshot import draw
from backend.storage import Storage
from backend.texts import text
from backend.vision import Frame, Zone

log = logging.getLogger("kinwatch.alerts")


def what_happened(rule: Rule) -> str:
    zone = rule.zone_object
    return {
        Condition.ABSENT_FROM_ZONE: f"left the {zone} and has not come back",
        Condition.PRESENT_IN_ZONE: f"was seen at the {zone}",
        Condition.ON_OBJECT: f"got on the {zone}",
        Condition.NO_PERSON_NEAR: (f"flame on, nobody near the {zone}" if rule.guard
                                   else f"nobody near the {zone}"),
    }[rule.condition]


def frame_statements(rule: Rule, n: int, has_before: bool = True) -> list[str]:
    """One factual statement per evidence frame. With `has_before`, the first frame is from before the
    condition started; without one (e.g. nobody was ever seen), every frame shows the condition."""
    who = "a person" if rule.subject.value == "person" else f"a {rule.subject.value}"
    zone = rule.zone_object
    holds = {
        Condition.ABSENT_FROM_ZONE: f"no {rule.subject.value} is on or in the {zone}",
        Condition.PRESENT_IN_ZONE: f"{who} is at the {zone}",
        Condition.ON_OBJECT: f"{who} is on the {zone}",
        Condition.NO_PERSON_NEAR: f"no person is near the {zone}",
    }[rule.condition]
    before = {
        Condition.ABSENT_FROM_ZONE: f"{who} is on or in the {zone}",
        Condition.PRESENT_IN_ZONE: f"nobody is at the {zone}",
        Condition.ON_OBJECT: f"nothing is on the {zone}",
        Condition.NO_PERSON_NEAR: f"a person is near the {zone}",
    }[rule.condition]
    if has_before and n > 1:
        return [before] + [holds] * (n - 1)
    return [holds] * n


class Alerts:
    def __init__(self, *, storage: Storage, settings: Settings, clock: Clock, adapters: Adapters,
                 send, household_id: int) -> None:
        self.storage = storage
        self.settings = settings
        self.clock = clock
        self.adapters = adapters
        self.send = send  # Router.send: records a delivery row for every message
        self.household_id = household_id
        self._tasks: set[asyncio.Task] = set()

    def _members(self) -> dict[int, Member]:
        return {m.id: m for m in self.storage.members(self.household_id)}

    async def on_event(self, rule: Rule, fired: Transition, evidence: list[Frame], zone: Zone,
                       has_before: bool = True) -> int:
        now = self.clock.now()
        since = fired.data.get("since", now)
        facts = EventFacts(rule_id=rule.id, subject_name=rule.subject_name, subject=rule.subject,
                           zone_object=rule.zone_object, condition=rule.condition, what=what_happened(rule),
                           started_at_local=local_hhmm(since), met_at_local=local_hhmm(fired.ts),
                           duration_min=max(1, round(rule.duration_sec / 60)) if rule.duration_sec else 0,
                           visibility=fired.data.get("visibility", "good"), language=rule.language)
        latest = evidence[-1]
        photo = await asyncio.to_thread(draw, latest.jpeg, [zone.mask], numbered=False)
        event_id = self.storage.execute(
            "INSERT INTO events (rule_id, started_at, met_at, facts_json, status) VALUES (?, ?, ?, ?, 'sent')",
            (rule.id, since, fired.ts, facts.model_dump_json())).lastrowid
        folder = self.settings.data_path / "events" / str(event_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "snapshot.jpg").write_bytes(photo)
        self.storage.execute("UPDATE events SET snapshot_path = ? WHERE id = ?", (str(folder / "snapshot.jpg"), event_id))

        message = await self.adapters.spark.write_alert(facts)
        alert_id = self.storage.execute(
            "INSERT INTO alerts (event_id, text, language, next_escalation_at) VALUES (?, ?, ?, ?)",
            (event_id, message, rule.language, now + self.settings.effective_claim_timeout_sec)).lastrowid
        members = self._members()
        button = [Button(f"claim:{alert_id}", text("claim_button", rule.language))]
        recipients = [members[i] for i in rule.notify if i in members]
        await asyncio.gather(*(self.send(m, "alert", message, button, jpeg=photo, alert_id=alert_id)
                               for m in recipients))
        self.storage.add_timeline(self.household_id, now, "alert_sent",
                                  f"{local_hhmm(now)} alert sent to {', '.join(m.name for m in recipients)}",
                                  rule_id=rule.id, data={"event_id": event_id, "alert_id": alert_id, "text": message})

        task = asyncio.create_task(self._verify(event_id, rule, evidence, has_before))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return alert_id

    async def _verify(self, event_id: int, rule: Rule, evidence: list[Frame], has_before: bool) -> None:
        frames = [TimedFrame(ts=f.ts, jpeg=f.jpeg) for f in evidence]
        try:
            v = await self.adapters.spark.confirm_event(frames, frame_statements(rule, len(frames), has_before))
            result = {"consistent": v.consistent, "visible_summary": v.visible_summary, "confidence": v.confidence}
        except Exception as exc:  # advisory: an error changes nothing for the family
            result = {"error": str(exc)[:200]}
        self.storage.execute("UPDATE events SET verification_json = ? WHERE id = ?", (json.dumps(result), event_id))

    # --- claims -----------------------------------------------------------------------------------

    async def claim(self, alert_id: int, member: Member) -> None:
        now = self.clock.now()
        row = self.storage.query_one("SELECT a.language, e.rule_id FROM alerts a JOIN events e ON e.id = a.event_id"
                                     " WHERE a.id = ?", (alert_id,))
        if row is None:
            return await self.send(member, "info", text("fallback_reply", self.settings.household_language))
        lang = row["language"]
        won = self.storage.execute(
            "UPDATE alerts SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ? AND status = 'open'",
            (member.id, now, alert_id)).rowcount == 1
        if not won:
            holder = self.storage.query_one("SELECT m.name FROM alerts a JOIN members m ON m.id = a.claimed_by"
                                            " WHERE a.id = ?", (alert_id,))
            name = holder["name"] if holder else "?"
            return await self.send(member, "info", text("already_claimed", lang, name=name), alert_id=alert_id)
        self.storage.add_timeline(self.household_id, now, "alert_claimed", f"{local_hhmm(now)} {member.name} is handling it",
                                  rule_id=row["rule_id"], data={"alert_id": alert_id})
        await self.send(member, "info", text("claim_thanks", lang), alert_id=alert_id)
        alerted = {r["member_id"] for r in self.storage.query(
            "SELECT DISTINCT member_id FROM deliveries WHERE alert_id = ? AND kind = 'alert'", (alert_id,))}
        members = self._members()
        notice = text("claimed_notice", lang, name=member.name, time=templates.spoken_time(local_hhmm(now), lang))
        await asyncio.gather(*(self.send(members[i], "claim_notice", notice, alert_id=alert_id)
                               for i in alerted if i != member.id and i in members))
