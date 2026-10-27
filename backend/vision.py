"""Frame intake and the free change detector (PLAN.md §3.4).

Frames live only in memory: a ring buffer of the last ~60 s. The change detector compares a tiny
colour thumbnail with the last frame that was analysed, so SAM is only called when something in
view actually changed (or a heartbeat is due).
"""
from __future__ import annotations

import io
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

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
    thumb: np.ndarray  # uint8 RGB, THUMB_SIZE (colour: a red shirt on a blue sofa can match in grey)
    luminance: float  # mean grey level, 0-255
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
            small = im.convert("RGB").resize(THUMB_SIZE, Image.BILINEAR)
            thumb = np.asarray(small, dtype=np.uint8)
            luminance = float(np.asarray(small.convert("L"), dtype=np.float32).mean())
    except (UnidentifiedImageError, OSError) as exc:
        raise FrameError(f"not an image: {exc}") from exc
    return Frame(ts=ts, jpeg=jpeg, width=width, height=height, thumb=thumb,
                 luminance=luminance, captured_at=captured_at)


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
    """(mean difference, fraction of pixels that changed by more than PIXEL_DIFF), per pixel the largest
    change across the colour channels."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=-1)
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


# --- Mask geometry (PLAN.md §3.4) --------------------------------------------------------------

IN_ZONE_OVERLAP = 0.30
ON_OBJECT_OVERLAP = 0.50
ZONE_DILATE_PX = 8
NEAR_EXPAND = 0.75  # "near" = within 0.75 x the zone's width of its box
MIN_CONFIDENCE = 0.35


def colfill(mask: np.ndarray) -> np.ndarray:
    """Fill each column between its top and bottom pixel: restores the part of a zone hidden behind
    someone lying on it (Step 0: raw sofa mask overlapped the lying person 0.00; filled, 1.00)."""
    has = mask.any(axis=0)
    top = mask.argmax(axis=0)
    bottom = mask.shape[0] - 1 - mask[::-1].argmax(axis=0)
    rows = np.arange(mask.shape[0])[:, None]
    return (rows >= top) & (rows <= bottom) & has


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask
    img = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * px + 1))
    return np.asarray(img) > 0


def box_of(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


@dataclass(frozen=True)
class Zone:
    """A confirmed zone, prepared once: filled + dilated mask and its box."""

    mask: np.ndarray  # bool, frame-sized, as SAM returned it
    area: np.ndarray  # colfill + dilate: what "in the zone" tests against
    box: tuple[int, int, int, int]
    door: bool = False  # doors are judged by "near" (SAM outlines the leaf, not the doorway)

    @classmethod
    def from_mask(cls, mask: np.ndarray, concept: str = "") -> Zone:
        box = box_of(mask)
        if box is None:
            raise ValueError("empty zone mask")
        return cls(mask=mask, area=dilate(colfill(mask), ZONE_DILATE_PX), box=box, door="door" in concept)

    @property
    def shape(self) -> tuple[int, int]:
        return self.mask.shape


def overlap(subject: np.ndarray, area: np.ndarray) -> float:
    n = int(subject.sum())
    return float((subject & area).sum()) / n if n else 0.0


def is_in(subject: np.ndarray, zone: Zone) -> bool:
    return overlap(subject, zone.area) >= IN_ZONE_OVERLAP


def is_on(subject: np.ndarray, zone: Zone) -> bool:
    if overlap(subject, zone.area) < ON_OBJECT_OVERLAP:
        return False
    box = box_of(subject)
    if box is None:
        return False
    cut = box[3] - (box[3] - box[1]) // 4
    bottom = subject.copy()
    bottom[:cut] = False
    return overlap(bottom, zone.area) >= 0.5


def is_near(subject_box: tuple[int, int, int, int], zone: Zone) -> bool:
    x1, y1, x2, y2 = zone.box
    pad = NEAR_EXPAND * (x2 - x1 + 1)
    sx1, sy1, sx2, sy2 = subject_box
    return not (sx2 < x1 - pad or sx1 > x2 + pad or sy2 < y1 - pad or sy1 > y2 + pad)
