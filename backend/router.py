"""Inbound message routing by conversation state (PLAN.md §5.6). No AI classifier: buttons, numbers
and keywords only; anything else gets the fixed fallback reply.

Rule intake: text or voice note -> Muse Spark parse -> validation -> SAM outlines the zone on the
latest camera frame -> photo + restatement from the validated JSON with [Yes] [No] (PLAN.md §3.3).
Several candidates are numbered; "2" or "no, the other one" picks one. Nothing is armed until yes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from backend import templates
from backend.budget import BudgetExceeded
from backend.clock import Clock, local_hhmm
from backend.config import Settings
from backend.media import AudioError, voice_to_wav
from backend.messaging.base import Button, Inbound, Messenger, StatusUpdate
from backend.models import Adapters
from backend.models.mock_spark import detect_language
from backend.questions import look, sentence
from backend.rules import validate_parsed
from backend.schemas import Language, Member, Rule, RuleStatus
from backend.storage import Storage
from backend.snapshot import describe, draw
from backend.texts import text
from backend.vision import MIN_CONFIDENCE, FrameBuffer
from backend.zones import load_candidates, reference_frame, save_candidates

log = logging.getLogger("kinwatch.router")

CONFIRM_TTL_SEC = 60 * 60
CLARIFY_TTL_SEC = 10 * 60
MAX_CLARIFY_ROUNDS = 2
LANGUAGE_BIAS = ["Hindi", "Kannada", "English"]
STATUS_ORDER = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3}  # failed is handled apart
FRESH_FRAME_SEC = 20  # the confirmation photo must be this recent
MAX_ZONES = 5

YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "haan", "haa", "han", "ha", "ji", "sahi", "theek", "thik",
             "हाँ", "हां", "हा", "जी", "सही", "ठीक", "ಹೌದು", "howdu", "houdu", "sari", "ಸರಿ"}
NO_WORDS = {"no", "n", "nope", "nahi", "nahin", "na", "नहीं", "नही", "ना", "ಇಲ್ಲ", "illa", "beda", "ಬೇಡ"}

State = Literal["idle", "awaiting_confirmation", "awaiting_clarification"]


def _words(message: str) -> list[str]:
    # Drop punctuation and symbols only: `\w` would also drop Devanagari/Kannada vowel signs.
    t = unicodedata.normalize("NFC", message).casefold()
    return "".join(" " if unicodedata.category(ch)[0] in "PSZ" else ch for ch in t).split()


def is_yes(message: str) -> bool:
    w = _words(message)
    return 0 < len(w) <= 3 and w[0] in YES_WORDS


def is_no(message: str) -> bool:
    w = _words(message)
    return len(w) > 0 and w[0] in NO_WORDS


CLAIM_PHRASES = ("got it", "on it", "dekh lunga", "dekh lungi", "dekh leta", "dekh leti", "dekh raha", "dekh rahi",
                 "देख लूँगा", "देख लूंगा", "देख लूँगी", "देख लूंगी", "देख लेता", "देख लेती", "देख रहा", "देख रही",
                 "ನೋಡ್ತೀನಿ", "ನೋಡುತ್ತೇನೆ", "nodtini", "nodteeni")


def is_claim(message: str) -> bool:
    """A typed "I've got it" in place of the button. Short messages only, so a rule never matches."""
    w = _words(message)
    joined = f" {' '.join(w)} "
    return 0 < len(w) <= 5 and any(f" {p} " in joined for p in CLAIM_PHRASES)


MAX_QUESTION_WORDS = 15
QUESTION_STARTS = {"is", "are", "was", "does", "do", "has", "have", "can", "where", "who", "anyone", "anybody",
                   "kya", "क्या", "kahan", "कहाँ", "कहां", "koi", "कोई", "ಎಲ್ಲಿ", "ಯಾರಾದರೂ", "ಯಾರು"}
RULE_WORDS = {"if", "when", "whenever", "agar", "अगर", "jab", "जब", "ಆದರೆ"}
RULE_PHRASES = ("tell me", "let me know", "alert", "notify", "inform", "remind", "batana", "bata dena", "बताना",
                "बताओ", "बता देना", "ತಿಳಿಸು", "ಹೇಳು")


def is_question(message: str) -> bool:
    """A question about right now, not a rule ("tell me if…") in question form. Spark gets the final say."""
    w = _words(message)
    if not 0 < len(w) <= MAX_QUESTION_WORDS:
        return False
    joined = f" {' '.join(w)} "
    kannada_if = any(x.endswith("ರೆ") for x in w)  # ಬಂದ್ರೆ / ಬಂದರೆ: "if … comes"
    if set(w) & RULE_WORDS or kannada_if or any(f" {p} " in joined for p in RULE_PHRASES):
        return False
    kannada_yes_no = w[-1].endswith("ಾ") and any("ಀ" <= ch <= "೿" for ch in w[-1])  # ಇದ್ದಾರಾ, ಇದೆಯಾ
    return message.strip().endswith("?") or w[0] in QUESTION_STARTS or kannada_yes_no


LIST_WORDS = {"rules", "rule", "list", "niyam", "नियम", "ನಿಯಮ", "ನಿಯಮಗಳು"}
COMMAND_WORDS = {
    "pause": {"pause", "stop", "band", "roko", "बंद", "रोको", "ನಿಲ್ಲಿಸು"},
    "resume": {"resume", "start", "unpause", "chalu", "चालू", "शुरू", "ಚಾಲೂ", "ಶುರು"},
    "delete": {"delete", "remove", "hatao", "हटाओ", "ಅಳಿಸು"},
}


def rule_command(message: str) -> tuple[str, int | None] | None:
    """'rules' -> ('list', None); 'pause 3' / '3 pause' / 'नियम 3 बंद' -> ('pause', 3). No AI involved."""
    w = _words(message)
    if len(w) == 1 and w[0] in LIST_WORDS:
        return ("list", None)
    w = [x for x in w if x not in LIST_WORDS]  # "rule 3 pause", "नियम 3 बंद"
    numbers = [x for x in w if x.isdigit()]
    verbs = [action for x in w for action, vocab in COMMAND_WORDS.items() if x in vocab]
    if len(w) == 2 and len(numbers) == 1 and len(verbs) == 1:
        return (verbs[0], int(numbers[0]))
    return None


VIEW_WORDS = {"check", "camera", "photo", "picture", "snapshot", "view", "dikhao", "दिखाओ", "कैमरा", "ತೋರಿಸು", "ಕ್ಯಾಮೆರಾ"}
VIEW_PHRASES = ("can you see", "can u see", "what do you see", "what can you see", "kya dikh", "क्या दिख")


def wants_camera_view(message: str) -> bool:
    """'check', 'camera', 'can you see?': send back the latest frame. Free: no model is involved."""
    w = _words(message)
    joined = " ".join(w)
    return (0 < len(w) <= 4 and w[0] in VIEW_WORDS) or any(p in joined for p in VIEW_PHRASES)


@dataclass
class Conversation:
    member_id: int
    state: State = "idle"
    rule_id: int | None = None
    expires_at: float | None = None
    data: dict = field(default_factory=dict)


class Router:
    def __init__(self, *, storage: Storage, settings: Settings, clock: Clock, adapters: Adapters,
                 messenger: Messenger, household_id: int, frames: FrameBuffer | None = None) -> None:
        self.storage = storage
        self.settings = settings
        self.clock = clock
        self.adapters = adapters
        self.messenger = messenger
        self.household_id = household_id
        self._locks: dict[int, asyncio.Lock] = {}
        self._introduced: set[str] = set()
        self.frames = frames
        self.watcher = None  # set by the app: reloaded when a rule is armed
        self.alerts = None  # set by the app: handles "I've got it"

    async def introduce(self, inbound: Inbound) -> None:
        """Tell an unknown sender their address once, so the admin can add them to FAMILY_MEMBERS_JSON."""
        if inbound.sender_address in self._introduced:
            return
        self._introduced.add(inbound.sender_address)
        stranger = Member(id=0, household_id=self.household_id, name="(unknown)", channel=inbound.channel,
                          address=inbound.sender_address)
        lang = self._language(inbound.text)
        try:
            await self.messenger.send_text(stranger, text("introduce", lang, address=inbound.sender_address))
        except Exception as exc:  # not a household member: no delivery row, just a log line
            log.warning("couldn't introduce an unknown sender: %s", exc)

    # --- conversation state ---------------------------------------------------------------------

    def conversation(self, member_id: int) -> Conversation:
        row = self.storage.query_one("SELECT * FROM conversations WHERE member_id = ?", (member_id,))
        if row is None:
            return Conversation(member_id)
        conv = Conversation(member_id, row["state"], row["rule_id"], row["expires_at"], json.loads(row["data_json"]))
        if conv.expires_at is not None and conv.expires_at < self.clock.now():
            return Conversation(member_id)
        return conv

    def save(self, conv: Conversation) -> None:
        self.storage.execute(
            "INSERT INTO conversations (member_id, state, rule_id, expires_at, data_json) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(member_id) DO UPDATE SET state = excluded.state, rule_id = excluded.rule_id,"
            " expires_at = excluded.expires_at, data_json = excluded.data_json",
            (conv.member_id, conv.state, conv.rule_id, conv.expires_at, json.dumps(conv.data, ensure_ascii=False)),
        )

    def reset(self, member_id: int) -> None:
        self.save(Conversation(member_id))

    # --- sending --------------------------------------------------------------------------------

    async def send(self, member: Member, kind: str, message: str, buttons: list[Button] | None = None,
                   jpeg: bytes | None = None, alert_id: int | None = None) -> None:
        now = self.clock.now()
        try:
            if jpeg is not None:
                ref = await self.messenger.send_image(member, jpeg, message, buttons)
            else:
                ref = await self.messenger.send_text(member, message, buttons)
        except Exception as exc:
            self.storage.execute(
                "INSERT INTO deliveries (alert_id, member_id, kind, sent_at, status, error) VALUES (?, ?, ?, ?, 'failed', ?)",
                (alert_id, member.id, kind, now, str(exc)[:300]))
            self.storage.add_timeline(self.household_id, now, "delivery_failed", f"Message to {member.name} failed: {exc}")
            log.warning("send to member %s failed: %s", member.id, exc)
            return
        self.storage.execute(
            "INSERT INTO deliveries (alert_id, member_id, kind, provider_msg_id, sent_at) VALUES (?, ?, ?, ?, ?)",
            (alert_id, member.id, kind, ref.provider_msg_id, now))

    def apply_status(self, update: StatusUpdate) -> None:
        """Record sent/delivered/read/failed from a status webhook on the matching delivery row."""
        row = self.storage.query_one("SELECT id, member_id, status FROM deliveries WHERE provider_msg_id = ?",
                                     (update.provider_msg_id,))
        detail = f" (error {update.error_code}: {update.error_title})" if update.status == "failed" else ""
        log.info("WhatsApp status %s for message ...%s%s", update.status, update.provider_msg_id[-8:], detail)
        if row is None:
            return
        if update.status == "failed":
            error = f"{update.error_code}: {update.error_title}"
            self.storage.execute("UPDATE deliveries SET status = 'failed', error = ? WHERE id = ?", (error, row["id"]))
            self.storage.add_timeline(self.household_id, self.clock.now(), "delivery_failed",
                                      f"WhatsApp could not deliver message {row['id']} ({error})",
                                      data={"delivery_id": row["id"], "member_id": row["member_id"]})
        elif STATUS_ORDER.get(update.status, -1) > STATUS_ORDER.get(row["status"], -1):
            self.storage.execute("UPDATE deliveries SET status = ? WHERE id = ?", (update.status, row["id"]))

    # --- entry point ----------------------------------------------------------------------------

    async def handle(self, inbound: Inbound) -> None:
        member = self.storage.member_by_address(inbound.channel, inbound.sender_address)
        if member is None:
            log.info("ignoring message from an unknown sender (%s)", inbound)
            return
        lock = self._locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            lang = self._language(inbound.text)
            try:
                await self._dispatch(inbound, member, lang)
            except BudgetExceeded as exc:
                log.warning("budget refused while handling %s: %s", inbound, exc.reason)
                await self.send(member, "info", text("error", lang))
            except Exception:
                log.exception("failed to handle %s", inbound)
                await self.send(member, "info", text("error", lang))
            finally:
                self.storage.execute("UPDATE inbound_messages SET processed_at = ? WHERE provider_msg_id = ?",
                                     (self.clock.now(), inbound.provider_msg_id))

    def _language(self, message: str | None) -> Language:
        return detect_language(message) if message and message.strip() else self.settings.household_language

    async def _dispatch(self, inbound: Inbound, member: Member, lang: Language) -> None:
        conv = self.conversation(member.id)
        if inbound.kind == "button":  # the button title says nothing about the member's language
            return await self._button(inbound.button_payload or "", member, conv, self.settings.household_language)
        if inbound.kind == "voice":
            heard = await self._transcribe(inbound, member)
            if heard is None:
                return
            return await self._message(heard, "voice", member, conv)
        if inbound.kind == "text" and (inbound.text or "").strip():
            return await self._message(inbound.text.strip(), "text", member, conv)
        await self.send(member, "info", text("fallback_reply", lang))

    async def _transcribe(self, inbound: Inbound, member: Member) -> str | None:
        lang = self.settings.household_language
        try:
            audio, _mime = await self.messenger.download_media(inbound.media_ref or "")
            wav = await asyncio.to_thread(voice_to_wav, audio)
            transcript = await self.adapters.transcriber.transcribe(wav, LANGUAGE_BIAS)
        except (AudioError, KeyError) as exc:
            log.warning("voice note unusable: %s", exc)
            await self.send(member, "info", text("voice_failed", lang))
            return None
        heard = transcript.text.strip()
        if not heard:
            await self.send(member, "info", text("voice_failed", lang))
            return None
        await self.send(member, "info", text("heard", detect_language(heard), text=heard))
        return heard

    async def _message(self, message: str, kind: Literal["text", "voice"], member: Member, conv: Conversation) -> None:
        if wants_camera_view(message):
            return await self._camera_view(member, self._language(message))
        if is_claim(message) and self.alerts is not None and (alert_id := self.alerts.latest_for(member)):
            return await self.alerts.claim(alert_id, member)
        if command := rule_command(message):
            return await self._rule_command(*command, member, self._language(message))
        if conv.state == "awaiting_confirmation":
            return await self._confirmation_reply(message, member, conv)
        if conv.state == "awaiting_clarification":
            combined = f"{conv.data.get('text', '')} {message}".strip()
            return await self._new_rule(combined, conv.data.get("kind", kind), member,
                                        rounds=int(conv.data.get("rounds", 0)) + 1,
                                        transcript=conv.data.get("transcript") or (message if kind == "voice" else None))
        if is_question(message):
            return await self._question(message, kind, member)
        await self._new_rule(message, kind, member, rounds=0, transcript=message if kind == "voice" else None)

    async def _question(self, message: str, kind: Literal["text", "voice"], member: Member) -> None:
        """Ask the camera: one Spark read of the question, then SAM on the latest frame (see questions.py)."""
        lang = self._language(message)
        frame = self.frames.latest(self.clock.now(), FRESH_FRAME_SEC) if self.frames is not None else None
        if frame is None:
            return await self.send(member, "info", text("camera_offline", lang))
        q = await self.adapters.spark.parse_question(message)
        if q.kind == "rule":  # "can you tell me if…": a rule after all
            return await self._new_rule(message, kind, member, rounds=0,
                                        transcript=message if kind == "voice" else None)
        now = self.clock.now()
        record = {"question": message, "parsed": {"kind": q.kind, "subject": q.subject.value, "zone": q.zone_object,
                                                  "relation": q.relation, "language": q.language}}
        if q.kind == "unsupported":
            reply, photo = text("question_unsupported", q.language), frame.jpeg
        elif frame.dark:
            reply, photo = text("too_dark_now", q.language), frame.jpeg
        else:
            answer = await look(q, frame, self.adapters.segmenter, self.settings)
            record.update(answer.data, count=answer.count)
            photo = answer.photo
            reply = (sentence(q, answer.count, local_hhmm(frame.ts)) if answer.count is not None else
                     text("zone_not_in_view", q.language, zone=templates.zone_word(q.zone_object or "", q.language)))
        record["answer"] = reply
        self.storage.add_timeline(self.household_id, now, "question_answered", f"{member.name} asked: {message}",
                                  data=record)
        await self.send(member, "info", reply, jpeg=photo)

    async def _camera_view(self, member: Member, lang: Language) -> None:
        frame = self.frames.latest(self.clock.now(), FRESH_FRAME_SEC) if self.frames is not None else None
        if frame is None:
            return await self.send(member, "info", text("camera_offline", lang))
        caption = text("camera_view", lang)
        if frame.height > frame.width:
            caption += text("camera_view_upright", lang)
        await self.send(member, "info", caption, jpeg=frame.jpeg)

    # --- rule commands: rules / pause N / resume N / delete N ---------------------------------------

    async def _rule_command(self, action: str, number: int | None, member: Member, lang: Language) -> None:
        if action == "list":
            return await self._list_rules(member, lang)
        rule = self.storage.get_rule(number) if number is not None else None
        if (rule is None or rule.household_id != self.household_id
                or rule.status not in (RuleStatus.ARMED, RuleStatus.PAUSED)):
            return await self.send(member, "info", text("rule_unknown", lang, n=str(number)))
        n, now = str(rule.id), self.clock.now()
        if action == "pause":
            if rule.status == RuleStatus.PAUSED:
                return await self.send(member, "info", text("rule_already_paused", lang, n=n))
            self.storage.execute("UPDATE rules SET status = 'paused' WHERE id = ?", (rule.id,))
            self.storage.add_timeline(self.household_id, now, "rule_paused", f"Rule {n} paused by {member.name}",
                                      rule_id=rule.id)
            reply = text("rule_paused", lang, n=n)
        elif action == "resume":
            if rule.status == RuleStatus.ARMED:
                return await self.send(member, "info", text("rule_already_on", lang, n=n))
            if not load_candidates(self.settings.data_path, rule.id):
                return await self.send(member, "info", text("rule_no_zone", lang, n=n))
            # A fresh start: a timer left over from before the pause must not fire the moment it's back.
            self.storage.execute("DELETE FROM rule_state WHERE rule_id = ?", (rule.id,))
            self.storage.execute("UPDATE rules SET status = 'armed', armed_at = ? WHERE id = ?", (now, rule.id))
            self.storage.add_timeline(self.household_id, now, "rule_resumed", f"Rule {n} resumed by {member.name}",
                                      rule_id=rule.id)
            names = self._names(rule, self.storage.members(self.household_id))
            reply = text("rule_resumed", lang, n=n, summary=templates.summary(rule, names))
        else:
            self.storage.execute("UPDATE rules SET status = 'deleted' WHERE id = ?", (rule.id,))
            self.storage.execute("DELETE FROM rule_state WHERE rule_id = ?", (rule.id,))
            self.storage.add_timeline(self.household_id, now, "rule_deleted", f"Rule {n} deleted by {member.name}",
                                      rule_id=rule.id)
            reply = text("rule_deleted", lang, n=n)
        if self.watcher is not None:
            self.watcher.reload()
        await self.send(member, "info", reply)

    async def _list_rules(self, member: Member, lang: Language) -> None:
        rows = self.storage.query("SELECT id FROM rules WHERE household_id = ? AND status IN ('armed', 'paused')"
                                  " ORDER BY id", (self.household_id,))
        rules = [r for r in (self.storage.get_rule(row["id"]) for row in rows) if r is not None]
        if not rules:
            return await self.send(member, "info", text("rules_none", lang))
        members = self.storage.members(self.household_id)
        lines = [text("rules_header", lang)]
        for rule in rules:
            state = text("rule_on" if rule.status == RuleStatus.ARMED else "rule_off", lang)
            lines.append(f"{rule.id} · {state} · {templates.summary(rule, self._names(rule, members))}")
        lines.append(text("rules_help", lang, n=str(rules[0].id)))
        await self.send(member, "info", "\n".join(lines))

    async def _confirmation_reply(self, message: str, member: Member, conv: Conversation) -> None:
        rule = self.storage.get_rule(conv.rule_id) if conv.rule_id else None
        lang = rule.language if rule else self.settings.household_language
        words = _words(message)
        n = int(conv.data.get("n", 1))
        if len(words) == 1 and words[0].isdigit():
            return await self._select(conv, member, int(words[0]))
        if is_yes(message):
            return await self._arm(conv, member)
        if is_no(message):
            if len(words) > 1 and n > 1 and rule is not None:  # "no, the other bed"
                return await self._correct(conv, member, rule, message)
            return await self._decline(conv, member)
        await self.send(member, "info", text("fallback_reply", lang))

    async def _button(self, payload: str, member: Member, conv: Conversation, lang: Language) -> None:
        action, _, ident = payload.partition(":")
        if action == "claim" and ident.isdigit() and self.alerts is not None:
            return await self.alerts.claim(int(ident), member)
        rule_part, _, number = ident.partition(":")
        if conv.state == "awaiting_confirmation" and rule_part == str(conv.rule_id):
            if action == "yes":
                return await self._arm(conv, member)
            if action == "no":
                return await self._decline(conv, member)
            if action == "pick" and number.isdigit():
                return await self._select(conv, member, int(number))
        await self.send(member, "info", text("fallback_reply", lang))  # stale or unknown button

    # --- rules ----------------------------------------------------------------------------------

    async def _new_rule(self, message: str, kind: Literal["text", "voice"], member: Member, *, rounds: int,
                        transcript: str | None) -> None:
        members = self.storage.members(self.household_id)
        parsed = await self.adapters.spark.parse_rule(message, [m.name for m in members], member.name)
        result = validate_parsed(parsed, original_text=message, members=members, sender=member,
                                 household_id=self.household_id, input_kind=kind, transcript=transcript)
        now = self.clock.now()
        self.storage.add_timeline(self.household_id, now, "rule_parsed", f"{member.name}: {message}",
                                  data={"parsed": parsed.model_dump(mode="json"), "result": result.kind,
                                        "notes": result.notes})
        if result.kind == "unsupported":
            self.reset(member.id)
            return await self.send(member, "info", result.question or text("unsupported", parsed.language))
        if result.kind == "clarify":
            if rounds >= MAX_CLARIFY_ROUNDS:
                self.reset(member.id)
                return await self.send(member, "info", text("not_set", parsed.language))
            self.save(Conversation(member.id, "awaiting_clarification", None, now + CLARIFY_TTL_SEC,
                                   {"text": message, "kind": kind, "rounds": rounds, "transcript": transcript}))
            return await self.send(member, "info", result.question or text("clarify_generic", parsed.language))

        rule = result.rule
        assert rule is not None
        frame = self.frames.latest(now, FRESH_FRAME_SEC) if self.frames is not None else None
        if frame is None:
            self.reset(member.id)
            return await self.send(member, "info", text("camera_offline", rule.language))
        concept = self.settings.sam_concept(rule.zone_object)
        found = await self.adapters.segmenter.segment(frame.jpeg, concept, purpose="segment_zone")
        found = [i for i in found if i.confidence is None or i.confidence >= MIN_CONFIDENCE][:MAX_ZONES]
        if not found:
            self.reset(member.id)
            return await self.send(member, "info", text("zone_not_found", rule.language,
                                                        zone=templates.zone_word(rule.zone_object, rule.language)),
                                   jpeg=frame.jpeg)
        found.sort(key=lambda i: i.box.x1)  # number them left to right
        rule = rule.model_copy(update={"status": RuleStatus.AWAITING_CONFIRMATION})
        rule_id = self.storage.insert_rule(rule, now)
        rule = rule.model_copy(update={"id": rule_id})
        save_candidates(self.settings.data_path, rule_id, frame.jpeg, [i.mask for i in found])
        self.save(Conversation(member.id, "awaiting_confirmation", rule_id, now + CONFIRM_TTL_SEC,
                               {"n": len(found), "selected": 1 if len(found) == 1 else None}))
        if len(found) == 1:
            return await self._send_confirmation(member, rule, 1)
        names = self._names(rule, members)
        question = await self.adapters.spark.restate(rule, len(found), names)
        image = await asyncio.to_thread(draw, frame.jpeg, [i.mask for i in found], numbered=True)
        buttons = [Button(f"pick:{rule_id}:{k}", str(k)) for k in range(1, min(len(found), 3) + 1)]
        await self.send(member, "confirm", question, buttons, jpeg=image)

    async def _send_confirmation(self, member: Member, rule: Rule, selected: int) -> None:
        """Photo with the chosen zone outlined + restatement from the validated JSON + [Yes] [No]."""
        masks = load_candidates(self.settings.data_path, rule.id)
        frame = reference_frame(self.settings.data_path, rule.id)
        names = self._names(rule, self.storage.members(self.household_id))
        restatement = await self.adapters.spark.restate(rule, 1, names)
        self.storage.execute("UPDATE rules SET restatement = ?, zone_instance = ? WHERE id = ?",
                             (restatement, selected, rule.id))
        buttons = [Button(f"yes:{rule.id}", text("button_yes", rule.language)),
                   Button(f"no:{rule.id}", text("button_no", rule.language))]
        image = None
        if frame is not None and 0 < selected <= len(masks):
            image = await asyncio.to_thread(draw, frame, [masks[selected - 1]], numbered=False)
        await self.send(member, "confirm", restatement, buttons, jpeg=image)

    async def _select(self, conv: Conversation, member: Member, number: int) -> None:
        rule = self.storage.get_rule(conv.rule_id) if conv.rule_id else None
        if rule is None:
            self.reset(member.id)
            return await self.send(member, "info", text("not_set", self.settings.household_language))
        if not 1 <= number <= int(conv.data.get("n", 1)):
            return await self.send(member, "info", text("pick_one", rule.language))
        conv.data["selected"] = number
        self.save(conv)
        await self._send_confirmation(member, rule, number)

    async def _correct(self, conv: Conversation, member: Member, rule: Rule, reply: str) -> None:
        masks = load_candidates(self.settings.data_path, rule.id)
        frame = reference_frame(self.settings.data_path, rule.id) or b""
        numbered = await asyncio.to_thread(draw, frame, masks, numbered=True) if frame and masks else frame
        width = masks[0].shape[1] if masks else 1
        fix = await self.adapters.spark.resolve_correction(numbered, describe(masks, width), reply,
                                                           conv.data.get("selected"))
        if fix.action == "select_instance" and fix.instance:
            return await self._select(conv, member, fix.instance)
        if fix.action == "reparse" and fix.new_text:
            self.storage.execute("UPDATE rules SET status = 'deleted' WHERE id = ?", (rule.id,))
            self.reset(member.id)
            return await self._new_rule(fix.new_text, rule.input_kind, member, rounds=0, transcript=None)
        await self._decline(conv, member)

    @staticmethod
    def _names(rule: Rule, members: list[Member]) -> list[str]:
        by_id = {m.id: m.name for m in members}
        return [by_id[i] for i in rule.notify if i in by_id]

    async def _arm(self, conv: Conversation, member: Member) -> None:
        now = self.clock.now()
        rule = self.storage.get_rule(conv.rule_id) if conv.rule_id else None
        if rule is None:
            self.reset(member.id)
            return await self.send(member, "info", text("not_set", self.settings.household_language))
        selected = conv.data.get("selected")
        if selected is None:
            return await self.send(member, "info", text("pick_one", rule.language))
        self.storage.execute("UPDATE rules SET status = 'armed', armed_at = ?, zone_instance = ? WHERE id = ?",
                             (now, selected, rule.id))
        self.storage.add_timeline(self.household_id, now, "rule_armed", f"Rule {rule.id} armed by {member.name}",
                                  rule_id=rule.id, data={"rule": rule.rule_json(), "zone_instance": selected})
        self.reset(member.id)
        if self.watcher is not None:
            self.watcher.reload()
        await self.send(member, "info", text("armed", rule.language))

    async def _decline(self, conv: Conversation, member: Member) -> None:
        rule = self.storage.get_rule(conv.rule_id) if conv.rule_id else None
        lang = rule.language if rule else self.settings.household_language
        if rule is not None:
            self.storage.execute("UPDATE rules SET status = 'deleted' WHERE id = ?", (rule.id,))
            self.storage.add_timeline(self.household_id, self.clock.now(), "rule_declined",
                                      f"Rule {rule.id} not confirmed by {member.name}", rule_id=rule.id)
        self.reset(member.id)
        await self.send(member, "info", text("not_set", lang))
