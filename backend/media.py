"""Audio conversion for Voice Transcribe.

WhatsApp voice notes are OGG/Opus. libsndfile (via `soundfile`) decodes them, so ffmpeg isn't needed
for this path (PLAN.md §7.4). Output is what Voice Transcribe accepts: mono 16-bit PCM WAV at 16 kHz
(24 kHz input is kept as is).
"""
from __future__ import annotations

import io

import numpy as np
import soundfile as sf

ACCEPTED_RATES = (16000, 24000)
TARGET_RATE = 16000
MAX_SECONDS = 600  # the API rejects audio longer than 10 minutes


class AudioError(ValueError):
    pass


def voice_to_wav(data: bytes) -> bytes:
    try:
        audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError) as exc:
        raise AudioError(f"can't decode audio: {exc}") from exc
    if len(audio) == 0:
        raise AudioError("empty audio")
    mono = audio.mean(axis=1)
    if rate not in ACCEPTED_RATES:
        n_out = int(round(len(mono) * TARGET_RATE / rate))
        mono = np.interp(np.linspace(0, len(mono) - 1, n_out), np.arange(len(mono)), mono).astype(np.float32)
        rate = TARGET_RATE
    if len(mono) / rate > MAX_SECONDS:
        raise AudioError("voice note longer than 10 minutes")
    buf = io.BytesIO()
    sf.write(buf, mono, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()
