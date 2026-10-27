"""Telegram Bot API adapter: the fallback channel while Meta's business verification blocks WhatsApp
(PLAN.md §6.2). Same Messenger protocol, so the rest of Kinwatch doesn't change.

Webhook requests carry X-Telegram-Bot-Api-Secret-Token (set with setWebhook), checked in constant time.
A member's address is their Telegram chat id. Telegram reports no delivery statuses: a successful
send means the message is in the chat.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

import httpx

from backend.config import Settings
from backend.messaging.base import Button, Inbound, SendError, SentRef, StatusUpdate, WebhookRejected, fit_buttons
from backend.schemas import Member

API = "https://api.telegram.org"
SEND_TIMEOUT_SEC = 20.0
MAX_TEXT = 4096
MAX_CAPTION = 1024


def webhook_secret(settings: Settings) -> str:
    """Derived from BTS_TOKEN so there's no extra setting; Telegram allows [A-Za-z0-9_-], 1-256 chars."""
    return hmac.new(settings.bts_token.get_secret_value().encode(), b"telegram-webhook", hashlib.sha256).hexdigest()


CALLBACK_PREFIX = "tg.cb."


def callback_query_id(inbound: Inbound) -> str | None:
    """The id to pass to answerCallbackQuery when this inbound is a tapped button."""
    pid = inbound.provider_msg_id
    return pid[len(CALLBACK_PREFIX):] if pid.startswith(CALLBACK_PREFIX) else None


def parse_update(update: dict) -> list[Inbound]:
    if cq := update.get("callback_query"):
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id") or (cq.get("from") or {}).get("id")
        return [Inbound(provider_msg_id=f"{CALLBACK_PREFIX}{cq['id']}", channel="telegram", sender_address=str(chat),
                        kind="button", ts=float((cq.get("message") or {}).get("date", 0)),
                        button_payload=cq.get("data"))]
    msg = update.get("message")
    if not msg or (msg.get("chat") or {}).get("type", "private") != "private":
        return []  # groups aren't used: each member talks to the bot directly (PLAN.md §6.1)
    base = {"provider_msg_id": f"tg.{msg['chat']['id']}.{msg['message_id']}", "channel": "telegram",
            "sender_address": str(msg["chat"]["id"]), "ts": float(msg.get("date", 0))}
    if "text" in msg:
        return [Inbound(kind="text", text=msg["text"], **base)]
    if voice := msg.get("voice") or msg.get("audio"):
        return [Inbound(kind="voice", media_ref=voice["file_id"], mime=voice.get("mime_type"), **base)]
    return [Inbound(kind="other", **base)]


class TelegramMessenger:
    channel = "telegram"

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        if settings.telegram_bot_token is None or not settings.telegram_bot_token.get_secret_value():
            raise RuntimeError("Telegram needs TELEGRAM_BOT_TOKEN in .env")
        self._token = settings.telegram_bot_token.get_secret_value()
        self._secret = webhook_secret(settings)
        self.http = http_client or httpx.AsyncClient(timeout=SEND_TIMEOUT_SEC)

    def _url(self, method: str) -> str:
        return f"{API}/bot{self._token}/{method}"

    async def call(self, method: str, *, data: dict | None = None, files: dict | None = None) -> dict:
        if files:
            r = await self.http.post(self._url(method), data=data, files=files)
        else:
            r = await self.http.post(self._url(method), json=data or {})
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 300 or not body.get("ok"):
            raise SendError(r.status_code, body.get("error_code"), body.get("description") or r.text[:300])
        return body.get("result") or {}

    async def send_text(self, to: Member, text: str, buttons: list[Button] | None = None) -> SentRef:
        data: dict = {"chat_id": to.address, "text": text[:MAX_TEXT]}
        if buttons:
            data["reply_markup"] = _keyboard(buttons)
        result = await self.call("sendMessage", data=data)
        return SentRef(provider_msg_id=f"tg.{to.address}.{result.get('message_id')}")

    async def send_image(self, to: Member, jpeg: bytes, caption: str, buttons: list[Button] | None = None) -> SentRef:
        data: dict = {"chat_id": to.address, "caption": caption[:MAX_CAPTION]}
        if buttons:
            data["reply_markup"] = json.dumps(_keyboard(buttons), ensure_ascii=False)
        result = await self.call("sendPhoto", data=data, files={"photo": ("kinwatch.jpg", jpeg, "image/jpeg")})
        return SentRef(provider_msg_id=f"tg.{to.address}.{result.get('message_id')}")

    async def download_media(self, ref: str) -> tuple[bytes, str]:
        info = await self.call("getFile", data={"file_id": ref})
        r = await self.http.get(f"{API}/file/bot{self._token}/{info['file_path']}")
        if r.status_code >= 300:
            raise SendError(r.status_code, None, "file download failed")
        return r.content, r.headers.get("content-type", "")

    async def acknowledge(self, callback_query_id: str) -> None:
        """Stops the spinner on a tapped button. Best effort."""
        try:
            await self.call("answerCallbackQuery", data={"callback_query_id": callback_query_id})
        except (SendError, httpx.HTTPError):
            pass

    def verify_and_parse(self, headers: Mapping[str, str], raw_body: bytes) -> tuple[list[Inbound], list[StatusUpdate]]:
        given = headers.get("x-telegram-bot-api-secret-token", "")
        if not hmac.compare_digest(given.encode(), self._secret.encode()):
            raise WebhookRejected("bad secret token")
        try:
            inbound = parse_update(json.loads(raw_body))
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookRejected("malformed update") from exc
        return inbound, []


def _keyboard(buttons: list[Button]) -> dict:
    return {"inline_keyboard": [[{"text": b.title, "callback_data": b.id} for b in fit_buttons(buttons)]]}
