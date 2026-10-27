import sqlite3

import pytest

from backend.schemas import Condition, Rule, Subject, TimeWindow
from backend.storage import Storage

TABLES = {"households", "members", "devices", "rules", "rule_state", "conversations", "events", "alerts",
          "deliveries", "inbound_messages", "timeline", "api_calls"}


def test_migrations_create_every_table_and_are_idempotent(tmp_path):
    db = Storage(tmp_path / "k.db")
    assert db.migrate() == ["001_init.sql"]
    assert db.migrate() == []
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES <= names
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    db.close()


def test_members_round_trip_and_hide_numbers_in_repr(storage):
    hid = storage.add_household("Home")
    storage.add_member(hid, "Priya", "910000000001", aliases=["प्रिया"], is_admin=True)
    storage.add_member(hid, "Rahul", "910000000002", escalation_order=1)
    members = storage.members(hid)
    assert [m.name for m in members] == ["Priya", "Rahul"]
    assert members[0].aliases == ["प्रिया"] and members[0].is_admin
    assert "910000000001" not in repr(members[0]) and "910000000001" not in str(members)
    assert storage.member_by_address("whatsapp", "910000000002").name == "Rahul"


def test_duplicate_member_address_is_rejected(storage):
    hid = storage.add_household("Home")
    storage.add_member(hid, "Priya", "910000000001")
    with pytest.raises(sqlite3.IntegrityError):
        storage.add_member(hid, "Priya again", "910000000001")


def test_rule_round_trip(storage):
    hid = storage.add_household("Home")
    mid = storage.add_member(hid, "Priya", "910000000001")
    rule = Rule(household_id=hid, created_by=mid, original_text="पापा...", subject=Subject.PERSON,
                subject_name="पापा", zone_object="bed", condition=Condition.ABSENT_FROM_ZONE, duration_sec=600,
                time_window=TimeWindow(start="22:00", end="06:00"), notify=[mid], language="hi")
    rid = storage.insert_rule(rule, now=1000.0)
    back = storage.get_rule(rid)
    assert back.id == rid and back.created_at == 1000.0
    assert back.rule_json() == rule.rule_json()


def test_transaction_rolls_back(storage):
    hid = storage.add_household("Home")
    with pytest.raises(RuntimeError):
        with storage.transaction() as conn:
            conn.execute("INSERT INTO timeline (household_id, ts, kind, text) VALUES (?, 1, 'x', 'y')", (hid,))
            raise RuntimeError("boom")
    assert storage.timeline(hid) == []


def test_timeline_newest_first(storage):
    hid = storage.add_household("Home")
    storage.add_timeline(hid, 10.0, "state", "Papa in bed")
    storage.add_timeline(hid, 20.0, "state", "bed count 1 → 0")
    assert [r["text"] for r in storage.timeline(hid)] == ["bed count 1 → 0", "Papa in bed"]
