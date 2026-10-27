"""Mock Muse Voice Transcribe: fixture transcripts by audio hash, else the main Hindi demo sentence."""
from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

from backend.models.base import Transcript

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "mock" / "asr.json"
DEFAULT_TRANSCRIPT = "पापा अगर रात को उठें और दस मिनट में वापस न आएँ तो मुझे और राहुल को बताना"


def _duration_ms(wav: bytes) -> int:
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return int(w.getnframes() * 1000 / w.getframerate())
    except (wave.Error, EOFError):
        return 0


class MockTranscriber:
    def __init__(self, fixtures: Path = FIXTURES) -> None:
        self._by_hash: dict[str, str] = json.loads(fixtures.read_text(encoding="utf-8")) if fixtures.exists() else {}

    async def transcribe(self, wav: bytes, language_bias: list[str]) -> Transcript:
        digest = hashlib.sha256(wav).hexdigest()
        return Transcript(text=self._by_hash.get(digest, DEFAULT_TRANSCRIPT), duration_ms=_duration_ms(wav))
