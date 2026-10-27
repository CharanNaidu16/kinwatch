"""Messaging channels (PLAN.md §6). MESSENGER=mock (default) or whatsapp."""
from __future__ import annotations

from backend.config import Settings
from backend.messaging.base import Messenger


def build_messenger(settings: Settings) -> Messenger:
    if settings.messenger == "whatsapp" and settings.mode == "live":
        from backend.messaging.whatsapp import WhatsAppMessenger

        return WhatsAppMessenger(settings)
    from backend.messaging.mock import MockMessenger

    return MockMessenger()
