import io
import wave

import numpy as np
import pytest
import soundfile as sf

from backend.media import AudioError, voice_to_wav


def _ogg_opus(seconds: float, rate: int, channels: int) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio = np.stack([tone] * channels, axis=1)
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="OGG", subtype="OPUS")
    return buf.getvalue()


@pytest.mark.parametrize("rate,channels,expected_rate", [(16000, 1, 16000), (48000, 2, 16000), (24000, 1, 24000)])
def test_whatsapp_style_opus_becomes_mono_pcm_wav(rate, channels, expected_rate):
    wav = voice_to_wav(_ogg_opus(2.0, rate, channels))
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, expected_rate)
        assert abs(w.getnframes() / w.getframerate() - 2.0) < 0.1


def test_garbage_is_rejected():
    with pytest.raises(AudioError):
        voice_to_wav(b"not audio at all")
