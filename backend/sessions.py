"""Recorded sessions for the replay link (PLAN.md §3.10).

Recording happens only between "record start" and "record stop" (admins, in the chat). A take is a
folder under data/sessions/ (gitignored): the behind-the-scenes state every couple of seconds, the
outlined camera frames, and every chat message with its photo, all as JSON lines stamped with
seconds since the start. scripts/sessions.py copies the takes worth keeping, trimmed and with
family names swapped for sample names, to fixtures/sessions/, which the replay page plays back.
Replay never calls a model or a messenger: it only shows what was recorded.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Callable
from pathlib import Path

from backend.clock import IST, Clock

TAKES_DIR = "sessions"  # under the data dir
KEPT_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sessions"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
FILE_RE = re.compile(r"^((frames|photos)/[a-z0-9_-]{1,40}\.jpg|session\.json|(states|frames|chat)\.jsonl)$")
MIN_FRAME_GAP_SEC = 1.0
MAX_SESSION_SEC = 20 * 60  # a forgotten "record stop" can't fill the disk
SAMPLE_NAMES = ["Priya", "Rahul", "Asha", "Vikram", "Meena", "Arjun"]


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def _write_jsonl(path: Path, item: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class Take:
    name: str
    folder: Path
    started: float
    baseline_usd: float
    last_frame: float = -1e9
    last_state: str = ""
    frames: int = 0
    messages: int = 0
    photos: int = 0
    meta: dict = field(default_factory=dict)


class SessionRecorder:
    """Records the live app while a take is running. Every method is a no-op when nothing is recorded."""

    def __init__(self, data_path: Path, clock: Clock, spent: Callable[[], float] = lambda: 0.0) -> None:
        self.root = data_path / TAKES_DIR
        self.clock = clock
        self.spent = spent  # total API spend so far, so a take shows only its own cost
        self.take: Take | None = None

    @property
    def active(self) -> bool:
        return self.take is not None

    def _t(self) -> float:
        assert self.take is not None
        return round(self.clock.now() - self.take.started, 2)

    def _next_name(self) -> str:
        n = 1
        while (self.root / f"take-{n}").exists():
            n += 1
        return f"take-{n}"

    def start(self) -> str:
        if self.take is not None:
            return self.take.name
        name = self._next_name()
        folder = self.root / name
        (folder / "frames").mkdir(parents=True)
        (folder / "photos").mkdir()
        now = self.clock.now()
        self.take = Take(name, folder, now, self.spent(),
                         meta={"name": name, "recorded_at": datetime.fromtimestamp(now, IST).isoformat(timespec="seconds")})
        self._save_meta()
        return name

    def _save_meta(self, **extra) -> None:
        assert self.take is not None
        t = self.take
        t.meta.update(extra, duration_sec=self._t(), frames=t.frames, messages=t.messages)
        (t.folder / "session.json").write_text(json.dumps(t.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def stop(self) -> dict | None:
        if self.take is None:
            return None
        self._save_meta()
        summary = dict(self.take.meta)
        self.take = None
        return summary

    def state(self, state: dict) -> None:
        """A behind-the-scenes snapshot; stored only when something changed."""
        if self.take is None:
            return
        if self._t() > MAX_SESSION_SEC:
            self.stop()
            return
        budget = state.get("budget") or {}
        if "total_usd" in budget:
            budget["session_usd"] = round(budget["total_usd"] - self.take.baseline_usd, 4)
        key = json.dumps({k: v for k, v in state.items() if k != "now"}, sort_keys=True, ensure_ascii=False)
        if key == self.take.last_state:
            return
        self.take.last_state = key
        _write_jsonl(self.take.folder / "states.jsonl", {"t": self._t(), "state": state})

    def frame(self, jpeg: bytes) -> None:
        """An outlined camera frame (what the page showed), at most one a second."""
        if self.take is None or self.clock.now() - self.take.last_frame < MIN_FRAME_GAP_SEC:
            return
        t = self.take
        t.last_frame = self.clock.now()
        t.frames += 1
        rel = f"frames/{t.frames:05d}.jpg"
        (t.folder / rel).write_bytes(jpeg)
        _write_jsonl(t.folder / "frames.jsonl", {"t": self._t(), "file": rel})

    def chat(self, direction: str, member: str, text: str, jpeg: bytes | None = None,
             buttons: list[str] | None = None) -> None:
        """One chat message: direction "in" (member -> Kinwatch) or "out" (Kinwatch -> member)."""
        if self.take is None:
            return
        t = self.take
        t.messages += 1
        item = {"t": self._t(), "dir": direction, "member": member, "text": text}
        if jpeg:
            t.photos += 1
            item["photo"] = f"photos/{t.photos:04d}.jpg"
            (t.folder / item["photo"]).write_bytes(jpeg)
        if buttons:
            item["buttons"] = buttons
        _write_jsonl(t.folder / "chat.jsonl", item)
        self._save_meta()

    # --- takes on disk -------------------------------------------------------------------------------

    def takes(self) -> list[dict]:
        return list_sessions(self.root)

    def delete(self, name: str) -> bool:
        folder = self.root / name
        if not valid_name(name) or not folder.is_dir() or (self.take is not None and self.take.name == name):
            return False
        shutil.rmtree(folder)
        return True


def list_sessions(root: Path) -> list[dict]:
    out = []
    for folder in sorted(root.glob("*/session.json")) if root.exists() else []:
        meta = json.loads(folder.read_text(encoding="utf-8"))
        size = sum(p.stat().st_size for p in folder.parent.rglob("*") if p.is_file())
        out.append({**meta, "name": folder.parent.name, "size_mb": round(size / 1e6, 1)})
    return out


# --- keeping a take for the replay link ------------------------------------------------------------------

def _scrub(value, names: dict[str, str]):
    if isinstance(value, str):
        for real, sample in names.items():
            value = re.sub(rf"\b{re.escape(real)}\b", sample, value)
        return value
    if isinstance(value, list):
        return [_scrub(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, names) for k, v in value.items()}
    return value


def keep(src: Path, dest: Path, names: dict[str, str], start: float = 0.0, end: float | None = None,
         title: str | None = None) -> dict:
    """Copy a take to `dest`, keeping only [start, end] seconds (re-timed from 0), with every family
    name replaced by its sample name. Frames and photos outside the window are left behind."""
    meta = json.loads((src / "session.json").read_text(encoding="utf-8"))
    end = meta.get("duration_sec", 0.0) if end is None else end
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "frames").mkdir(parents=True)
    (dest / "photos").mkdir()

    def window(items: list[dict]) -> list[dict]:
        return [{**i, "t": round(i["t"] - start, 2)} for i in items if start <= i["t"] <= end]

    states = read_jsonl(src / "states.jsonl")
    before = [s for s in states if s["t"] < start]
    kept_states = ([{**before[-1], "t": 0.0}] if before else []) + window(states)  # the picture as of `start`
    frames, chat = window(read_jsonl(src / "frames.jsonl")), window(read_jsonl(src / "chat.jsonl"))
    for item in frames + [c for c in chat if "photo" in c]:
        rel = item.get("file") or item["photo"]
        shutil.copy2(src / rel, dest / rel)
    for fname, items in (("states.jsonl", kept_states), ("frames.jsonl", frames), ("chat.jsonl", chat)):
        with (dest / fname).open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(_scrub(item, names), ensure_ascii=False, separators=(",", ":")) + "\n")
    out = {"name": dest.name, "title": title or dest.name.replace("-", " ").capitalize(),
           "recorded_at": meta.get("recorded_at"), "duration_sec": round(end - start, 2),
           "frames": len(frames), "messages": len(chat), "from_take": src.name}
    (dest / "session.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def sample_names(real: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Real family name -> sample name, in household order, unless overridden."""
    overrides = overrides or {}
    pool = iter([n for n in SAMPLE_NAMES if n not in overrides.values()])
    return {name: overrides.get(name) or next(pool, f"Member {i + 1}") for i, name in enumerate(real)}
