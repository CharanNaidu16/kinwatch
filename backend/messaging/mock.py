"""In-memory messenger for tests and development: an outbox you can read, inbound in WhatsApp's JSON shape."""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from backend.messaging.base import Button, Inbound, SentRef, StatusUpdate, WebhookRejected, fit_buttons
from backend.messaging.whatsapp import parse_webhook
from backend.schemas import Member


@dataclass
class Sent:
    member_id: int
    kind: str  # text | image
    text: str
    buttons: list[Button] = field(default_factory=list)
    jpeg: bytes | None = None
    provider_msg_id: str = ""


class MockMessenger:
    channel = "mock"

    def __init__(self) -> None:
        self.outbox: list[Sent] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self._ids = itertools.count(1)

    def _sent(self, item: Sent) -> SentRef:
        item.provider_msg_id = f"mock.{next(self._ids)}"
        self.outbox.append(item)
        return SentRef(item.provider_msg_id)

    async def send_text(self, to: Member, text: str, buttons: list[Button] | None = None) -> SentRef:
        return self._sent(Sent(to.id, "text", text, fit_buttons(buttons or [])))

    async def send_image(self, to: Member, jpeg: bytes, caption: str, buttons: list[Button] | None = None) -> SentRef:
        return self._sent(Sent(to.id, "image", caption, fit_buttons(buttons or []), jpeg))

    async def download_media(self, ref: str) -> tuple[bytes, str]:
        return self.media[ref]

    def verify_and_parse(self, headers: Mapping[str, str], raw_body: bytes) -> tuple[list[Inbound], list[StatusUpdate]]:
        try:
            inbound, statuses = parse_webhook(json.loads(raw_body))
        except (ValueError, KeyError) as exc:
            raise WebhookRejected("malformed body") from exc
        return [Inbound(**{**i.__dict__, "channel": "mock"}) for i in inbound], statuses

    def to(self, member_id: int) -> list[Sent]:
        return [s for s in self.outbox if s.member_id == member_id]
