"""Messenger protocol: the core never knows which chat app it is talking through (PLAN.md §3.7)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from backend.schemas import Member

MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20


@dataclass(frozen=True)
class Button:
    id: str  # payload that comes back when tapped, e.g. "yes:12", "claim:7"
    title: str


@dataclass(frozen=True)
class SentRef:
    provider_msg_id: str | None


@dataclass(frozen=True)
class Inbound:
    provider_msg_id: str
    channel: Literal["whatsapp", "telegram", "mock"]
    sender_address: str
    kind: Literal["text", "voice", "button", "other"]
    ts: float
    text: str | None = None
    button_payload: str | None = None
    media_ref: str | None = None
    mime: str | None = None

    def __repr__(self) -> str:  # no phone numbers in logs
        return f"Inbound({self.kind}, id={self.provider_msg_id})"


@dataclass(frozen=True)
class StatusUpdate:
    provider_msg_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    ts: float
    error_code: int | None = None
    error_title: str | None = None


class WebhookRejected(Exception):
    """Bad signature or malformed body: answer 401/400, process nothing."""


class SendError(Exception):
    def __init__(self, status_code: int, code: int | None, message: str) -> None:
        super().__init__(f"HTTP {status_code}, code {code}: {message}")
        self.status_code = status_code
        self.code = code


class Messenger(Protocol):
    channel: Literal["whatsapp", "telegram", "mock"]

    async def send_text(self, to: Member, text: str, buttons: list[Button] | None = None) -> SentRef: ...

    async def send_image(self, to: Member, jpeg: bytes, caption: str,
                         buttons: list[Button] | None = None) -> SentRef: ...

    async def download_media(self, ref: str) -> tuple[bytes, str]: ...

    def verify_and_parse(self, headers: Mapping[str, str], raw_body: bytes) -> tuple[list[Inbound], list[StatusUpdate]]:
        """Raises WebhookRejected on a bad signature."""
        ...


def fit_buttons(buttons: list[Button]) -> list[Button]:
    if len(buttons) > MAX_BUTTONS:
        raise ValueError(f"at most {MAX_BUTTONS} reply buttons")
    return [Button(b.id, b.title[:MAX_BUTTON_TITLE]) for b in buttons]
