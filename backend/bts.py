"""Data for the behind-the-scenes page (PLAN.md §3.10): what each Meta model did, and what the engine
is doing with it. Read-only, behind the BTS token. Never includes member addresses or tokens.
"""
from __future__ import annotations

import asyncio
import json

from backend import templates
from backend.clock import local_dt
from backend.engine import Phase
from backend.schemas import RuleStatus
from backend.snapshot import draw

RULES_SHOWN = 8
TIMELINE_SHOWN = 60
ALERTS_SHOWN = 6
FRESH_SUBJECTS_SEC = 15  # outline people from a SAM result at most this old

# Which models (or plain code) produced each timeline entry, for the badges on the page.
MODELS = {
    "rule_parsed": ["Muse Spark"], "rule_armed": ["SAM 3.1"], "question_answered": ["Muse Spark", "SAM 3.1"],
    "rule_pending": ["SAM 3.1", "engine"], "rule_returned": ["SAM 3.1", "engine"], "rule_fired": ["engine"],
    "rule_over": ["SAM 3.1", "engine"], "rule_baseline": ["SAM 3.1", "engine"], "alert_resent": ["engine"],
    "alert_escalated": ["engine"], "alert_expired": ["engine"],
}


def _clock(ts: float | None) -> str | None:
    return local_dt(ts).strftime("%H:%M:%S") if ts else None


def _json(raw: str | None) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _spark_output(db, household_id: int, rule) -> dict | None:
    """Muse Spark's raw parse for this rule: the rule_parsed entry just before the rule was stored."""
    row = db.query_one("SELECT data_json FROM timeline WHERE household_id = ? AND kind = 'rule_parsed' AND ts <= ?"
                       " ORDER BY ts DESC, id DESC LIMIT 1", (household_id, (rule.created_at or 0) + 1))
    return _json(row["data_json"]).get("parsed") if row else None


def _engine(rt, now: float) -> dict | None:
    if rt is None:
        return None
    e = rt.engine
    pending = e.phase == Phase.PENDING and e.since is not None
    return {"phase": e.phase.value, "since": _clock(e.since), "elapsed_sec": round(now - e.since, 1) if pending else None,
            "duration_sec": round(e.duration_sec, 1), "baseline": e.baseline, "count": rt.last_count,
            "has_zone": rt.zone is not None}


def _timeline_models(kind: str, data: dict) -> list[str]:
    if kind == "alert_sent":
        return ["Muse Spark"] if data.get("writer") == "spark" else ["template"]
    return MODELS.get(kind, [])


def state(app) -> dict:
    s, db, hid, now = app.settings, app.storage, app.household_id, app.clock.now()
    names = {m.id: m.name for m in db.members(hid)}
    latest = app.frames.latest()
    camera = {"connected": latest is not None and now - latest.ts < 20,
              "last_frame_sec": round(now - latest.ts, 1) if latest else None,
              "size": f"{latest.width}x{latest.height}" if latest else None,
              "dark": latest.dark if latest else None}

    rules = []
    rows = db.query("SELECT id FROM rules WHERE household_id = ? AND status IN ('armed', 'paused', 'awaiting_confirmation')"
                    " ORDER BY id DESC LIMIT ?", (hid, RULES_SHOWN))
    for row in rows:
        rule = db.get_rule(row["id"])
        if rule is None:
            continue
        notify = [names[i] for i in rule.notify if i in names]
        rules.append({
            "id": rule.id, "status": RuleStatus(rule.status).value, "input_kind": rule.input_kind,
            "original_text": rule.original_text, "transcript": rule.transcript,
            "created_by": names.get(rule.created_by), "created_at": _clock(rule.created_at),
            "spark_output": _spark_output(db, hid, rule), "rule_json": {**rule.rule_json(), "notify": notify},
            "sam_concept": s.sam_concept(rule.zone_object), "zone_instance": rule.zone_instance,
            "summary": templates.summary(rule, notify), "engine": _engine(app.watcher.runtimes.get(rule.id), now),
        })

    timeline = []
    for r in db.timeline(hid, TIMELINE_SHOWN):
        data = _json(r["data_json"])
        timeline.append({"time": _clock(r["ts"]), "kind": r["kind"], "text": r["text"],
                         "models": _timeline_models(r["kind"], data),
                         "detail": {k: data[k] for k in ("answer", "count", "writer", "reason") if k in data}})

    alerts = []
    for a in db.query("SELECT a.id, a.status, a.escalation_level, a.text, a.claimed_by, e.met_at, e.verification_json"
                      " FROM alerts a JOIN events e ON e.id = a.event_id ORDER BY a.id DESC LIMIT ?", (ALERTS_SHOWN,)):
        sent_to = [names.get(d["member_id"]) for d in db.query(
            "SELECT member_id FROM deliveries WHERE alert_id = ? AND kind IN ('alert', 'resend', 'escalation')"
            " GROUP BY member_id ORDER BY MIN(id)", (a["id"],))]
        check = _json(a["verification_json"])
        alerts.append({"id": a["id"], "status": a["status"], "level": a["escalation_level"], "text": a["text"],
                       "at": _clock(a["met_at"]), "claimed_by": names.get(a["claimed_by"]), "sent_to": sent_to,
                       "check": {k: check[k] for k in ("consistent", "visible_summary", "confidence") if k in check}})

    purposes = [{"model": r["model"], "purpose": r["purpose"], "calls": r["n"], "usd": round(r["usd"], 4)}
                for r in db.query("SELECT model, purpose, COUNT(*) AS n, COALESCE(SUM(est_cost_usd), 0) AS usd"
                                  " FROM api_calls WHERE mode = 'live' GROUP BY model, purpose ORDER BY model, n DESC")]
    return {
        "now": _clock(now),
        "mode": {"adapters": "live" if app.adapters.live else "mock", "demo_mode": s.demo_mode,
                 "time_scale": s.effective_time_scale, "messenger": s.messenger,
                 "claim_timeout_sec": s.effective_claim_timeout_sec},
        "camera": camera, "family": list(names.values()), "rules": rules, "timeline": timeline, "alerts": alerts,
        "budget": {**app.budget.summary(), "by_purpose": purposes},
    }


async def frame(app) -> bytes | None:
    """The latest camera frame: armed zones in green, people SAM found recently in blue."""
    latest = app.frames.latest()
    if latest is None:
        return None
    shape = (latest.height, latest.width)
    zones = [rt.zone.mask for rt in app.watcher.runtimes.values() if rt.zone is not None and rt.zone.shape == shape]
    subjects = []
    for ts, instances in app.watcher.last_seen.values():
        if instances and latest.ts - ts <= FRESH_SUBJECTS_SEC:
            subjects += [i.mask for i in instances if i.mask.shape == shape]
    return await asyncio.to_thread(draw, latest.jpeg, zones, numbered=False, subjects=subjects)
