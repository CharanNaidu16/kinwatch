"""Inbound message routing by conversation state (PLAN.md §5.6). No AI classifier: buttons, numbers
and keywords only; anything else gets the fixed fallback reply.

Rule intake today: text or voice note -> Muse Spark parse -> validation -> restatement with
[Yes] [No]. The confirmation snapshot (SAM outlines, PLAN.md §3.3) slots in before the restatement
on Thu 29 Oct.
"""
from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from backend.budget import BudgetExceeded
from backend.clock import Clock
from backend.config import Settings
from backend.media import AudioError, voice_to_wav
from backend.messaging.base import Button, Inbound, Messenger, StatusUpdate
from backend.models import Adapters
from backend.models.mock_spark import detect_language
from backend.rules import validate_parsed
from backend.schemas import Language, Member, Rule, RuleStatus
from backend.storage import Storage
from backend.texts import text

log = logging.getLogger("kinwatch.router")

CONFIRM_TTL_SEC = 60 * 60
CLARIFY_TTL_SEC = 10 * 60
MAX_CLARIFY_ROUNDS = 2
LANGUAGE_BIAS = ["Hindi", "Kannada", "English"]
STATUS_ORDER = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3}  # failed is handled apart

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


@dataclass
class Conversation:
    member_id: int
    state: State = "idle"
    rule_id: int | None = None
    expires_at: float | None = None
    data: dict = field(default_factory=dict)


class Router:
    def __init__(self, *, storage: Storage, settings: Settings, clock: Clock, adapters: Adapters,
                 messenger: Messenger, household_id: int) -> None:
        self.storage = storage
        self.settings = settings
        self.clock = clock
        self.adapters = adapters
        self.messenger = messenger
        self.household_id = household_id
        self._locks: dict[int, asyncio.Lock] = {}
        self._introduced: set[str] = set()

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
        if conv.state == "awaiting_confirmation":
            if is_yes(message):
                return await self._arm(conv, member)
            if is_no(message):
                return await self._decline(conv, member)
            rule = self.storage.get_rule(conv.rule_id) if conv.rule_id else None
            lang = rule.language if rule else self.settings.household_language
            return await self.send(member, "info", text("fallback_reply", lang))
        if conv.state == "awaiting_clarification":
            combined = f"{conv.data.get('text', '')} {message}".strip()
            return await self._new_rule(combined, conv.data.get("kind", kind), member,
                                        rounds=int(conv.data.get("rounds", 0)) + 1,
                                        transcript=conv.data.get("transcript") or (message if kind == "voice" else None))
        await self._new_rule(message, kind, member, rounds=0, transcript=message if kind == "voice" else None)

    async def _button(self, payload: str, member: Member, conv: Conversation, lang: Language) -> None:
        action, _, ident = payload.partition(":")
        if conv.state == "awaiting_confirmation" and ident == str(conv.rule_id):
            if action == "yes":
                return await self._arm(conv, member)
            if action == "no":
                return await self._decline(conv, member)
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
        names = self._names(rule, members)
        restatement = await self.adapters.spark.restate(rule, 1, names)
        rule = rule.model_copy(update={"status": RuleStatus.AWAITING_CONFIRMATION, "restatement": restatement})
        rule_id = self.storage.insert_rule(rule, now)
        self.save(Conversation(member.id, "awaiting_confirmation", rule_id, now + CONFIRM_TTL_SEC))
        buttons = [Button(f"yes:{rule_id}", text("button_yes", rule.language)),
                   Button(f"no:{rule_id}", text("button_no", rule.language))]
        await self.send(member, "confirm", restatement, buttons)

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
        self.storage.execute("UPDATE rules SET status = 'armed', armed_at = ? WHERE id = ?", (now, rule.id))
        self.storage.add_timeline(self.household_id, now, "rule_armed", f"Rule {rule.id} armed by {member.name}",
                                  rule_id=rule.id, data={"rule": rule.rule_json()})
        self.reset(member.id)
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
