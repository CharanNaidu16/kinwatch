"""Create the household and its members from FAMILY_MEMBERS_JSON (phone numbers never leave .env + DB)."""
from __future__ import annotations

import hashlib

from backend.config import Settings
from backend.storage import Storage


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_device(storage: Storage, household_id: int, settings: Settings) -> int:
    """The camera phone, identified by DEVICE_TOKEN (only its hash is stored)."""
    h = token_hash(settings.device_token.get_secret_value())
    row = storage.query_one("SELECT id FROM devices WHERE token_hash = ?", (h,))
    if row:
        return row["id"]
    return storage.execute("INSERT INTO devices (household_id, name, token_hash) VALUES (?, ?, ?)",
                           (household_id, "camera phone", h)).lastrowid


def ensure_household(storage: Storage, settings: Settings) -> int:
    """Return the household id, creating it and adding any configured members not yet present."""
    row = storage.query_one("SELECT id FROM households ORDER BY id LIMIT 1")
    household_id = row["id"] if row else storage.add_household("Home", settings.household_language)
    known = {(m.channel, m.address) for m in storage.members(household_id)}
    for order, fm in enumerate(settings.family_members_json):
        if (fm.channel, fm.address) in known:
            continue
        storage.add_member(household_id, fm.name, fm.address, channel=fm.channel, aliases=fm.aliases,
                           escalation_order=order, is_admin=fm.is_admin)
    return household_id
