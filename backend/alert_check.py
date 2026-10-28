"""Checks on alert text written by Muse Spark (PLAN.md §3.6). Any problem means the template is sent.

The model writes from structured facts only, but constrained decoding guarantees shape, not meaning,
so code decides: the right script, the start time present, no invented numbers, no banned words,
short enough to read at a glance.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from backend.schemas import EventFacts, Language

BANNED_FILE = Path(__file__).resolve().parent / "prompts" / "banned_terms.txt"
MAX_CHARS = 300
SCRIPT = {"hi": ("ऀ", "ॿ"), "kn": ("ಀ", "೿")}  # Devanagari, Kannada
ENGLISH_PREFIXES = ("diagnos", "symptom")  # also match "diagnosed", "symptoms"


@lru_cache(maxsize=1)
def banned_terms() -> dict[str, list[str]]:
    terms: dict[str, list[str]] = {"en": [], "hi": [], "kn": []}
    for line in BANNED_FILE.read_text(encoding="utf-8").splitlines():
        lang, sep, term = line.partition(":")
        if line.startswith("#") or not sep or lang.strip() not in terms:
            continue
        terms[lang.strip()].append(unicodedata.normalize("NFC", term.strip()).casefold())
    return terms


def _ascii_digits(message: str) -> str:
    """Devanagari and Kannada numerals count as numbers too ('२:०३' is '2:03')."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() else ch for ch in message)


def _hour_minute(hhmm: str) -> tuple[int, int]:
    h, m = (int(x) for x in hhmm.split(":"))
    return h, m


def _mentions_time(message: str, hhmm: str) -> bool:
    hour, minute = _hour_minute(hhmm)
    hours = {hour, hour % 12 or 12}
    if minute:
        return any(re.search(rf"(?<!\d)0?{h}[:.]{minute:02d}(?!\d)", message) for h in hours)
    return any(re.search(rf"(?<!\d)0?{h}(?![\d])", message) for h in hours)


def _allowed_numbers(facts: EventFacts) -> set[int]:
    allowed = {facts.duration_min, 0}
    for hhmm in (facts.started_at_local, facts.met_at_local):
        hour, minute = _hour_minute(hhmm)
        allowed |= {hour, hour % 12 or 12, minute}
    return allowed


def _banned_hits(message: str, lang: Language) -> list[str]:
    folded = unicodedata.normalize("NFC", message).casefold()
    hits = []
    for term in banned_terms()["en"]:  # English words can turn up in any language (Hinglish)
        pattern = rf"\b{re.escape(term)}" + ("" if term in ENGLISH_PREFIXES else r"\b")
        if re.search(pattern, folded):
            hits.append(term)
    if lang in ("hi", "kn"):
        hits += [t for t in banned_terms()[lang] if t in folded]
    return hits


def _script_ok(message: str, lang: Language) -> bool:
    letters = [ch for ch in message if ch.isalpha()]
    if not letters:
        return False
    if lang == "en":
        return sum(ch.isascii() for ch in letters) / len(letters) >= 0.9
    lo, hi = SCRIPT[lang]
    return sum(lo <= ch <= hi for ch in letters) / len(letters) >= 0.5


def _emoji_ok(message: str) -> bool:
    rest = message.removeprefix("⚠️").removeprefix("⚠")
    return not any(unicodedata.category(ch) == "So" or ch in "️‍" for ch in rest)


def problems(message: str, facts: EventFacts) -> list[str]:
    """Why this alert text can't be sent as is; an empty list means it can."""
    out = []
    if not message.strip():
        return ["empty"]
    if len(message) > MAX_CHARS:
        out.append(f"longer than {MAX_CHARS} characters")
    if not _script_ok(message, facts.language):
        out.append(f"not written in {facts.language}")
    digits = _ascii_digits(message)
    if not _mentions_time(digits, facts.started_at_local):
        out.append(f"start time {facts.started_at_local} missing")
    invented = sorted({int(n) for n in re.findall(r"\d+", digits)} - _allowed_numbers(facts))
    if invented:
        out.append(f"numbers not in the facts: {invented}")
    if hits := _banned_hits(message, facts.language):
        out.append(f"banned words: {hits}")
    if not _emoji_ok(message):
        out.append("emoji other than a leading ⚠️")
    if re.search(r"https?://|www\.", message):
        out.append("link")
    return out
