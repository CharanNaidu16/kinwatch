"""Frame intake and the free change detector (PLAN.md §3.4).

Frames live only in memory: a ring buffer of the last ~60 s. The change detector compares a tiny
grayscale thumbnail with the last frame that was analysed, so SAM is only called when something in
view actually changed (or a heartbeat is due).
"""
from __future__ import annotations

import io
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

THUMB_SIZE = (160, 120)
MAX_FRAME_BYTES = 2_000_000
MAX_SIDE = 4096
BUFFER_FRAMES = 40  # ~60 s at one frame per 1.5 s

# Tuned on recorded frames later (PLAN.md §3.4); start values from the plan.
MEAN_DIFF_THRESHOLD = 6.0
PIXEL_DIFF = 25
CHANGED_FRACTION = 0.02
DARK_LUMINANCE = 25.0


class FrameError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    ts: float  # when the server received it (Clock time)
    jpeg: bytes
    width: int
    height: int
    thumb: np.ndarray  # uint8 grayscale, THUMB_SIZE
    luminance: float  # mean of thumb, 0-255
    captured_at: float | None = None  # phone's clock, informational only

    @property
    def dark(self) -> bool:
        return self.luminance < DARK_LUMINANCE

    @property
    def shape(self) -> str:
        return f"{self.width}x{self.height}"


def load_frame(jpeg: bytes, ts: float, captured_at: float | None = None) -> Frame:
    if not jpeg:
        raise FrameError("empty body")
    if len(jpeg) > MAX_FRAME_BYTES:
        raise FrameError("frame too large")
    try:
        with Image.open(io.BytesIO(jpeg)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP"):
                raise FrameError(f"unsupported image format {im.format}")
            width, height = im.size
            if max(width, height) > MAX_SIDE:
                raise FrameError("frame dimensions too large")
            thumb = np.asarray(im.convert("L").resize(THUMB_SIZE, Image.BILINEAR), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise FrameError(f"not an image: {exc}") from exc
    return Frame(ts=ts, jpeg=jpeg, width=width, height=height, thumb=thumb,
                 luminance=float(thumb.mean()), captured_at=captured_at)


class FrameBuffer:
    def __init__(self, maxlen: int = BUFFER_FRAMES) -> None:
        self._frames: deque[Frame] = deque(maxlen=maxlen)

    def add(self, frame: Frame) -> None:
        self._frames.append(frame)

    def latest(self, now: float | None = None, max_age_sec: float | None = None) -> Frame | None:
        if not self._frames:
            return None
        frame = self._frames[-1]
        if now is not None and max_age_sec is not None and now - frame.ts > max_age_sec:
            return None
        return frame

    def since(self, ts: float) -> list[Frame]:
        return [f for f in self._frames if f.ts >= ts]

    def __len__(self) -> int:
        return len(self._frames)


def frame_difference(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(mean absolute difference, fraction of pixels that changed by more than PIXEL_DIFF)."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(diff.mean()), float((diff > PIXEL_DIFF).mean())


class ChangeDetector:
    def __init__(self) -> None:
        self._reference: Frame | None = None

    def changed(self, frame: Frame) -> bool:
        ref = self._reference
        if ref is None or ref.shape != frame.shape:
            return True
        mean, fraction = frame_difference(ref.thumb, frame.thumb)
        return mean > MEAN_DIFF_THRESHOLD or fraction > CHANGED_FRACTION

    def mark_analysed(self, frame: Frame) -> None:
        self._reference = frame
