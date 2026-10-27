"""Messaging channels (PLAN.md §6). MESSENGER=mock (default), whatsapp or telegram."""
from __future__ import annotations

from backend.config import Settings
from backend.messaging.base import Messenger


def build_messenger(settings: Settings) -> Messenger:
    if settings.messenger == "whatsapp" and settings.mode == "live":
        from backend.messaging.whatsapp import WhatsAppMessenger

        return WhatsAppMessenger(settings)
    if settings.messenger == "telegram" and settings.mode == "live":
        from backend.messaging.telegram import TelegramMessenger

        return TelegramMessenger(settings)
    from backend.messaging.mock import MockMessenger

    return MockMessenger()
