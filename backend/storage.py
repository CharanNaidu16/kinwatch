"""SQLite access: plain sqlite3, WAL mode, numbered migrations, no ORM (PLAN.md §3.12)."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from backend.schemas import Member, Rule

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit, with explicit BEGIN in transaction().
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        self._conn.close()

    # --- low level ------------------------------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT, so read-then-write sequences can't interleave."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # --- migrations -----------------------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply pending migrations in filename order. Returns the names applied."""
        self.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
        done = {r["name"] for r in self.query("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with self._lock:
                # Each migration file runs as one transaction together with its bookkeeping row.
                try:
                    self._conn.executescript(
                        "BEGIN;\n" + path.read_text(encoding="utf-8")
                        + f"\nINSERT INTO schema_migrations VALUES ('{path.name}', datetime('now'));\nCOMMIT;"
                    )
                except sqlite3.Error:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
            applied.append(path.name)
        return applied

    # --- households and members -----------------------------------------------------------------

    def add_household(self, name: str, default_language: str = "hi") -> int:
        cur = self.execute("INSERT INTO households (name, default_language) VALUES (?, ?)", (name, default_language))
        return int(cur.lastrowid)

    def add_member(
        self,
        household_id: int,
        name: str,
        address: str,
        *,
        channel: str = "whatsapp",
        aliases: list[str] | None = None,
        escalation_order: int = 0,
        is_admin: bool = False,
    ) -> int:
        cur = self.execute(
            "INSERT INTO members (household_id, name, channel, address, aliases_json, escalation_order, is_admin)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id, name, channel, address, json.dumps(aliases or [], ensure_ascii=False),
             escalation_order, int(is_admin)),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _member(row: sqlite3.Row) -> Member:
        return Member(
            id=row["id"], household_id=row["household_id"], name=row["name"], channel=row["channel"],
            address=row["address"], aliases=json.loads(row["aliases_json"]),
            escalation_order=row["escalation_order"], is_admin=bool(row["is_admin"]),
            last_inbound_at=row["last_inbound_at"],
        )

    def members(self, household_id: int) -> list[Member]:
        rows = self.query("SELECT * FROM members WHERE household_id = ? ORDER BY escalation_order, id", (household_id,))
        return [self._member(r) for r in rows]

    def member_by_address(self, channel: str, address: str) -> Member | None:
        row = self.query_one("SELECT * FROM members WHERE channel = ? AND address = ?", (channel, address))
        return self._member(row) if row else None

    # --- rules ----------------------------------------------------------------------------------

    def insert_rule(self, rule: Rule, now: float) -> int:
        cur = self.execute(
            "INSERT INTO rules (household_id, created_by, original_text, input_kind, transcript, rule_json,"
            " zone_instance, restatement, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule.household_id, rule.created_by, rule.original_text, rule.input_kind, rule.transcript,
             rule.model_dump_json(), rule.zone_instance, rule.restatement, rule.status.value, now),
        )
        return int(cur.lastrowid)

    def get_rule(self, rule_id: int) -> Rule | None:
        row = self.query_one("SELECT * FROM rules WHERE id = ?", (rule_id,))
        if row is None:
            return None
        rule = Rule.model_validate_json(row["rule_json"])
        return rule.model_copy(update={
            "id": row["id"], "status": row["status"], "zone_instance": row["zone_instance"],
            "restatement": row["restatement"], "created_at": row["created_at"], "armed_at": row["armed_at"],
        })

    # --- timeline -------------------------------------------------------------------------------

    def add_timeline(self, household_id: int, ts: float, kind: str, text: str,
                     rule_id: int | None = None, data: dict | None = None) -> int:
        cur = self.execute(
            "INSERT INTO timeline (household_id, ts, rule_id, kind, text, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (household_id, ts, rule_id, kind, text, json.dumps(data or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def timeline(self, household_id: int, limit: int = 50) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM timeline WHERE household_id = ? ORDER BY ts DESC, id DESC LIMIT ?", (household_id, limit)
        )
