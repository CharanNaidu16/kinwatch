"""Builders for WhatsApp webhook payloads used across tests."""
from __future__ import annotations

import hashlib
import hmac
import json


def envelope(messages: list[dict] | None = None, statuses: list[dict] | None = None) -> dict:
    value: dict = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"}}
    if messages:
        value["messages"] = messages
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}


def text(sender: str, body: str, msg_id: str = "wamid.in1", ts: int = 1793300000) -> dict:
    return {"from": sender, "id": msg_id, "timestamp": str(ts), "type": "text", "text": {"body": body}}


def button(sender: str, payload: str, title: str = "Yes", msg_id: str = "wamid.btn1") -> dict:
    return {"from": sender, "id": msg_id, "timestamp": "1793300000", "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": payload, "title": title}}}


def voice(sender: str, media_id: str, msg_id: str = "wamid.voice1") -> dict:
    return {"from": sender, "id": msg_id, "timestamp": "1793300000", "type": "audio",
            "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True}}


def status(msg_id: str, state: str, code: int | None = None) -> dict:
    s = {"id": msg_id, "status": state, "timestamp": "1793300001", "recipient_id": "910000000001"}
    if code is not None:
        s["errors"] = [{"code": code, "title": "Message undeliverable"}]
    return s


def signed(secret: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}
