"""WhatsApp Cloud API adapter (Graph API, PLAN.md §6.1).

Webhooks are verified with HMAC-SHA256 of the raw body using the app secret. Outbound images are
uploaded first and sent by media id. Delivery is only known from `statuses` webhooks: a 200 from
the send endpoint means Meta accepted the message, nothing more.
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

GRAPH = "https://graph.facebook.com"
SEND_TIMEOUT_SEC = 20.0
MAX_TEXT = 4096
MAX_INTERACTIVE_BODY = 1024


def signature_ok(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> tuple[list[Inbound], list[StatusUpdate]]:
    inbound: list[Inbound] = []
    statuses: list[StatusUpdate] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for m in value.get("messages", []) or []:
                inbound.append(_inbound(m))
            for s in value.get("statuses", []) or []:
                errors = s.get("errors") or [{}]
                if s.get("status") not in ("sent", "delivered", "read", "failed"):
                    continue
                statuses.append(StatusUpdate(provider_msg_id=s["id"], status=s["status"], ts=float(s.get("timestamp", 0)),
                                             error_code=errors[0].get("code"),
                                             error_title=errors[0].get("title") or errors[0].get("message")))
    return inbound, statuses


def _inbound(m: dict) -> Inbound:
    base = {"provider_msg_id": m["id"], "channel": "whatsapp", "sender_address": m["from"],
            "ts": float(m.get("timestamp", 0))}
    kind = m.get("type")
    if kind == "text":
        return Inbound(kind="text", text=(m.get("text") or {}).get("body", ""), **base)
    if kind == "audio":
        audio = m.get("audio") or {}
        return Inbound(kind="voice", media_ref=audio.get("id"), mime=audio.get("mime_type"), **base)
    if kind == "interactive":
        reply = (m.get("interactive") or {}).get("button_reply") or {}
        return Inbound(kind="button", button_payload=reply.get("id"), text=reply.get("title"), **base)
    if kind == "button":  # quick-reply button on a template message
        button = m.get("button") or {}
        return Inbound(kind="button", button_payload=button.get("payload"), text=button.get("text"), **base)
    return Inbound(kind="other", **base)


class WhatsAppMessenger:
    channel = "whatsapp"

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        missing = [name for name, value in (("WA_PHONE_NUMBER_ID", settings.wa_phone_number_id),
                                            ("WA_ACCESS_TOKEN", settings.wa_access_token),
                                            ("WA_APP_SECRET", settings.wa_app_secret)) if not value]
        if missing:
            raise RuntimeError(f"WhatsApp needs {', '.join(missing)} in .env")
        self.base = f"{GRAPH}/{settings.wa_graph_version}"
        self.phone_id = settings.wa_phone_number_id
        self._token = settings.wa_access_token.get_secret_value()
        self._app_secret = settings.wa_app_secret.get_secret_value()
        self.http = http_client or httpx.AsyncClient(timeout=SEND_TIMEOUT_SEC)

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _post_message(self, to: Member, body: dict) -> SentRef:
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.address, **body}
        r = await self.http.post(f"{self.base}/{self.phone_id}/messages", headers=self._auth, json=payload)
        data = _json(r)
        if r.status_code >= 300:
            err = data.get("error") or {}
            raise SendError(r.status_code, err.get("code"), err.get("message") or r.text[:300])
        messages = data.get("messages") or [{}]
        return SentRef(provider_msg_id=messages[0].get("id"))

    async def upload_media(self, content: bytes, mime: str, filename: str) -> str:
        r = await self.http.post(f"{self.base}/{self.phone_id}/media", headers=self._auth,
                                 data={"messaging_product": "whatsapp", "type": mime},
                                 files={"file": (filename, content, mime)})
        data = _json(r)
        if r.status_code >= 300 or "id" not in data:
            err = data.get("error") or {}
            raise SendError(r.status_code, err.get("code"), err.get("message") or r.text[:300])
        return data["id"]

    async def send_text(self, to: Member, text: str, buttons: list[Button] | None = None) -> SentRef:
        if buttons:
            return await self._post_message(to, {"type": "interactive", "interactive": {
                "type": "button", "body": {"text": text[:MAX_INTERACTIVE_BODY]},
                "action": {"buttons": _reply_buttons(buttons)}}})
        return await self._post_message(to, {"type": "text", "text": {"body": text[:MAX_TEXT], "preview_url": False}})

    async def send_image(self, to: Member, jpeg: bytes, caption: str, buttons: list[Button] | None = None) -> SentRef:
        media_id = await self.upload_media(jpeg, "image/jpeg", "kinwatch.jpg")
        if buttons:
            return await self._post_message(to, {"type": "interactive", "interactive": {
                "type": "button", "header": {"type": "image", "image": {"id": media_id}},
                "body": {"text": caption[:MAX_INTERACTIVE_BODY]}, "action": {"buttons": _reply_buttons(buttons)}}})
        return await self._post_message(to, {"type": "image", "image": {"id": media_id, "caption": caption[:MAX_INTERACTIVE_BODY]}})

    async def download_media(self, ref: str) -> tuple[bytes, str]:
        meta = await self.http.get(f"{self.base}/{ref}", headers=self._auth)
        info = _json(meta)
        if meta.status_code >= 300 or "url" not in info:
            raise SendError(meta.status_code, (info.get("error") or {}).get("code"), "media lookup failed")
        r = await self.http.get(info["url"], headers=self._auth)
        if r.status_code >= 300:
            raise SendError(r.status_code, None, "media download failed")
        return r.content, info.get("mime_type") or r.headers.get("content-type", "")

    def verify_and_parse(self, headers: Mapping[str, str], raw_body: bytes) -> tuple[list[Inbound], list[StatusUpdate]]:
        if not signature_ok(self._app_secret, raw_body, headers.get("x-hub-signature-256")):
            raise WebhookRejected("bad signature")
        try:
            payload = json.loads(raw_body)
        except ValueError as exc:
            raise WebhookRejected("body is not JSON") from exc
        return parse_webhook(payload)


def _reply_buttons(buttons: list[Button]) -> list[dict]:
    return [{"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in fit_buttons(buttons)]


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
