"""Muse Voice Transcribe: POST /v1/asr/transcribe, multipart (PLAN.md §7.1; test calls 9-10).

Only RIFF/WAVE mono 16-bit PCM at 16 or 24 kHz is accepted; media.voice_to_wav produces that.
The `request` part must include "audioEncoding": "WAV" (call 9 returned 400 without it).
"""
from __future__ import annotations

import io
import json
import wave

import httpx

from backend.budget import Budget, Usage
from backend.config import Settings
from backend.models.base import Transcript
from backend.models.meta_common import MetaAPIError, with_retries
from backend.recorder import Recorder

ASR_TIMEOUT_SEC = 45.0


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


class MetaTranscriber:
    def __init__(self, settings: Settings, budget: Budget, *, recorder: Recorder | None = None,
                 http_client: httpx.AsyncClient | None = None) -> None:
        if settings.meta_api_key is None or not settings.meta_api_key.get_secret_value():
            raise RuntimeError("META_API_KEY is not set; use USE_MOCKS=true or add the key to .env")
        self.model = settings.asr_model
        self.url = settings.meta_base_url.rstrip("/") + "/asr/transcribe"
        self._auth = {"Authorization": f"Bearer {settings.meta_api_key.get_secret_value()}"}
        self.budget = budget
        self.recorder = recorder
        self.http = http_client or httpx.AsyncClient(timeout=ASR_TIMEOUT_SEC)

    async def transcribe(self, wav: bytes, language_bias: list[str]) -> Transcript:
        seconds = wav_seconds(wav)
        request = {"model": self.model, "audioEncoding": "WAV", "languageBias": language_bias}

        async def attempt() -> dict:
            async with self.budget.guard(self.model, "transcribe", Usage(audio_sec=seconds)) as call:
                r = await self.http.post(self.url, headers=self._auth, files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": ("voice.wav", wav, "audio/wav"),
                })
                if r.status_code >= 300:
                    raise MetaAPIError(r.status_code, r.text)
                data = r.json()
                call.usage = Usage(audio_sec=(data.get("audioDurationMs") or seconds * 1000) / 1000)
                return data

        data = await with_retries(attempt)
        text = data.get("transcript")
        if text is None:  # fall back to joining turns if the top-level field is ever missing
            text = " ".join(t.get("transcript") or t.get("text", "") for t in data.get("turns", []))
        text = " ".join(text.split())
        if self.recorder:
            self.recorder.asr(wav, text)
        return Transcript(text=text, duration_ms=int(data.get("audioDurationMs") or seconds * 1000))
