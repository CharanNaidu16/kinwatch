"""Events, alerts and claims (PLAN.md §3.6, §3.8).

When a rule fires: record the event, write the alert (Muse Spark, vetted by alert_check, else the
template; PLAN.md §3.6), send
the photo with an "I've got it" button to everyone on the rule, and check the frames with Muse
Spark in the background. The check is advisory only and never holds an alert back (test call 3).
Claims are atomic: SQLite serialises writers, so exactly one of two simultaneous taps wins.

Escalation: each claim timeout without a claim moves the alert one level. Level 1 re-sends it to
everyone already alerted; later levels bring in the next household member not yet alerted (or
re-send when nobody is left). After MAX_ESCALATIONS timeouts the alert expires with a final notice;
a late "I've got it" still counts. Deadlines live in SQLite, so a restart loses nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from backend import templates
from backend.alert_check import problems
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

MAX_ESCALATIONS = 3  # timeouts before an unclaimed alert expires (PLAN.md §3.8)
CLAIMABLE = ("open", "expired")  # a late claim still tells the others who is handling it
ALERT_WRITE_TIMEOUT_SEC = 15.0  # past this the template goes out: an alert never waits on the writer
RECENT_ALERT_SEC = 6 * 60 * 60  # a typed "got it" applies to an alert sent to that member within this


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

    def _alerted(self, alert_id: int) -> set[int]:
        """Members who were sent this alert (first send, re-send or escalation), failed sends included."""
        return {r["member_id"] for r in self.storage.query(
            "SELECT DISTINCT member_id FROM deliveries WHERE alert_id = ? AND kind IN ('alert', 'resend', 'escalation')",
            (alert_id,))}

    def latest_for(self, member: Member) -> int | None:
        """The member's most recent claimable alert, for a typed "I've got it" without the button."""
        row = self.storage.query_one(
            "SELECT a.id FROM alerts a JOIN deliveries d ON d.alert_id = a.id"
            " WHERE d.member_id = ? AND d.kind IN ('alert', 'resend', 'escalation') AND d.sent_at >= ?"
            " AND a.status IN (?, ?) ORDER BY a.id DESC LIMIT 1",
            (member.id, self.clock.now() - RECENT_ALERT_SEC, *CLAIMABLE))
        return row["id"] if row else None

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

        message, writer = await self._write(facts)
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
                                  rule_id=rule.id, data={"event_id": event_id, "alert_id": alert_id, "text": message,
                                                         **writer})

        task = asyncio.create_task(self._verify(event_id, rule, evidence, has_before))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return alert_id

    async def _write(self, facts: EventFacts) -> tuple[str, dict]:
        """Spark's wording when it passes every check; the fixed template otherwise (and always when the
        alert carries a visibility or check-in note, which the template words exactly)."""
        fallback = templates.alert(facts)
        if facts.visibility != "good" or facts.check_in:
            return fallback, {"writer": "template", "reason": "alert carries a note"}
        try:
            written = (await asyncio.wait_for(self.adapters.spark.write_alert(facts), ALERT_WRITE_TIMEOUT_SEC)).strip()
        except Exception as exc:  # timeout, budget refusal, API error: the family is still told, right away
            log.warning("alert writer failed (%s); sending the template", type(exc).__name__)
            return fallback, {"writer": "template", "reason": type(exc).__name__}
        if issues := problems(written, facts):
            log.info("alert text rejected (%s); sending the template", "; ".join(issues))
            return fallback, {"writer": "template", "reason": "; ".join(issues), "rejected": written[:400]}
        return written, {"writer": "spark"}

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
            "UPDATE alerts SET status = 'claimed', claimed_by = ?, claimed_at = ?, next_escalation_at = NULL"
            " WHERE id = ? AND status IN (?, ?)", (member.id, now, alert_id, *CLAIMABLE)).rowcount == 1
        if not won:
            holder = self.storage.query_one("SELECT m.name FROM alerts a JOIN members m ON m.id = a.claimed_by"
                                            " WHERE a.id = ?", (alert_id,))
            name = holder["name"] if holder else "?"
            return await self.send(member, "info", text("already_claimed", lang, name=name), alert_id=alert_id)
        self.storage.add_timeline(self.household_id, now, "alert_claimed", f"{local_hhmm(now)} {member.name} is handling it",
                                  rule_id=row["rule_id"], data={"alert_id": alert_id})
        await self.send(member, "info", text("claim_thanks", lang), alert_id=alert_id)
        alerted = self._alerted(alert_id)
        members = self._members()
        notice = text("claimed_notice", lang, name=member.name, time=templates.spoken_time(local_hhmm(now), lang))
        await asyncio.gather(*(self.send(members[i], "claim_notice", notice, alert_id=alert_id)
                               for i in alerted if i != member.id and i in members))

    # --- escalation -------------------------------------------------------------------------------

    async def escalate(self, now: float) -> None:
        """Move every open alert whose claim deadline has passed one level (called by the app's ticker)."""
        rows = self.storage.query(
            "SELECT a.id, a.text, a.language, a.escalation_level, e.rule_id, e.met_at, e.snapshot_path"
            " FROM alerts a JOIN events e ON e.id = a.event_id"
            " WHERE a.status = 'open' AND a.next_escalation_at <= ? ORDER BY a.next_escalation_at", (now,))
        for row in rows:
            try:
                await self._escalate(row, now)
            except Exception:  # one bad alert must not stop the others
                log.exception("escalation of alert %s failed", row["id"])

    async def _escalate(self, row, now: float) -> None:
        alert_id, lang = row["id"], row["language"]
        level = row["escalation_level"] + 1
        timeout = self.settings.effective_claim_timeout_sec
        expire = level > MAX_ESCALATIONS
        moved = self.storage.execute(
            "UPDATE alerts SET escalation_level = ?, next_escalation_at = ?, status = ?"
            " WHERE id = ? AND status = 'open' AND escalation_level = ?",
            (level, None if expire else now + timeout, "expired" if expire else "open", alert_id, level - 1)).rowcount
        if moved != 1:
            return  # claimed (or moved) since we looked
        members = self._members()
        alerted = [members[i] for i in sorted(self._alerted(alert_id)) if i in members]
        if expire:
            notice = text("alert_expired", lang, time=templates.spoken_time(local_hhmm(row["met_at"]), lang))
            await asyncio.gather(*(self.send(m, "expired", notice, alert_id=alert_id) for m in alerted))
            self.storage.add_timeline(self.household_id, now, "alert_expired",
                                      f"{local_hhmm(now)} nobody claimed the alert", rule_id=row["rule_id"],
                                      data={"alert_id": alert_id})
            return

        path = Path(row["snapshot_path"]) if row["snapshot_path"] else None
        photo = path.read_bytes() if path is not None and path.exists() else None
        button = [Button(f"claim:{alert_id}", text("claim_button", lang))]
        alerted_ids = {m.id for m in alerted}
        newcomer = next((m for m in members.values()
                         if m.id not in alerted_ids and m.channel == self.settings.messenger), None)
        if level >= 2 and newcomer is not None:
            names = ", ".join(m.name for m in alerted)
            message = f"{text('escalation', lang, names=names)}\n\n{row['text']}"
            await self.send(newcomer, "escalation", message, button, jpeg=photo, alert_id=alert_id)
            self.storage.add_timeline(self.household_id, now, "alert_escalated",
                                      f"{local_hhmm(now)} no answer from {names}: alert sent to {newcomer.name}",
                                      rule_id=row["rule_id"], data={"alert_id": alert_id, "level": level})
            return
        wait = templates.spoken_duration(int(level * timeout), lang)
        message = f"{text('resend', lang, wait=wait)}\n\n{row['text']}"
        await asyncio.gather(*(self.send(m, "resend", message, button, jpeg=photo, alert_id=alert_id)
                               for m in alerted))
        self.storage.add_timeline(self.household_id, now, "alert_resent",
                                  f"{local_hhmm(now)} still unclaimed after {wait}: re-sent to "
                                  f"{', '.join(m.name for m in alerted)}",
                                  rule_id=row["rule_id"], data={"alert_id": alert_id, "level": level})
