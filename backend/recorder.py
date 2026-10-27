"""Saves real Meta API responses as fixtures (RECORD_FIXTURES=true), so each paid call becomes a free test.

Family names are replaced with {MEMBER:n} placeholders before anything is written: real names and
phone numbers live only in .env and the database, never in the repo (PLAN.md §0). Mock adapters
read these files back (mock_spark, mock_sam, mock_transcribe).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "recorded"


def frame_key(jpeg: bytes, concept: str) -> str:
    return f"{hashlib.sha256(jpeg).hexdigest()[:16]}_{re.sub(r'[^a-z0-9]+', '_', concept.lower()).strip('_')}"


def audio_key(wav: bytes) -> str:
    return hashlib.sha256(wav).hexdigest()


def scrub(text: str, names: list[list[str]]) -> str:
    """Replace each member's name and aliases (names[i] = [name, *aliases]) with {MEMBER:i+1}."""
    for i, variants in enumerate(names):
        for v in sorted((v for v in variants if v), key=len, reverse=True):
            text = re.sub(re.escape(v), f"{{MEMBER:{i + 1}}}", text, flags=re.IGNORECASE)
    return text


def fill(text: str, members: list[str]) -> str:
    """Inverse of scrub for the current household (placeholders beyond it are left as they are)."""
    def sub(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        return members[i] if 0 <= i < len(members) else m.group(0)
    return re.sub(r"\{MEMBER:(\d+)\}", sub, text)


def sam_path(root: Path, jpeg: bytes, concept: str) -> Path:
    return root / "sam" / f"{frame_key(jpeg, concept)}.txt"


class Recorder:
    def __init__(self, names: list[list[str]], root: Path = RECORDED) -> None:
        self.names = names
        self.root = root
        self._lock = threading.Lock()

    def _update_json(self, name: str, update) -> None:
        path = self.root / name
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            path.write_text(json.dumps(update(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def spark_parse(self, text: str, rule: dict) -> None:
        entry = {"text": scrub(text, self.names), "response": json.loads(scrub(json.dumps(rule, ensure_ascii=False), self.names))}

        def update(data: list | None) -> list:
            data = [e for e in (data or []) if e["text"] != entry["text"]]
            return [*data, entry]
        self._update_json("spark_parse_rule.json", update)

    def asr(self, wav: bytes, transcript: str) -> None:
        def update(data: dict | None) -> dict:
            return {**(data or {}), audio_key(wav): scrub(transcript, self.names)}
        self._update_json("asr.json", update)

    def sam(self, jpeg: bytes, concept: str, raw_output: str) -> None:
        path = sam_path(self.root, jpeg, concept)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw_output, encoding="utf-8")
