"""Mock SAM 3.1: segments synthetic frames by colour, so tests and dev runs need no API calls.

A synthetic scene paints each object as a flat rectangle in its concept's colour; each colour in a
concept's palette is one instance (two beds = two blue shades). `synthetic_frame()` builds them.
Real recorded SAM responses (from Wed 28 Oct) will be served from fixtures before this fallback.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

from backend.models.base import Box, Instance

# concept -> instance colours (RGB). Kept far apart so JPEG artefacts can't blur one into another.
PALETTE: dict[str, list[tuple[int, int, int]]] = {
    "person": [(220, 30, 30), (250, 250, 250)],  # 2nd colour can't arise from blending red/blue edges
    "bed": [(30, 60, 230), (0, 150, 220)],
    "gas stove": [(110, 110, 110)],
    "flame": [(255, 160, 0)],
    "main door": [(130, 80, 20)],
    "sofa": [(30, 170, 60)],
    "dog": [(240, 240, 120)],
}
PALETTE["door"] = PALETTE["main door"]
PALETTE["stove"] = PALETTE["gas stove"]

TOLERANCE = 40  # max per-channel distance from the key colour
MIN_PIXELS = 200  # smaller blobs (JPEG edge artefacts) are treated as noise
BACKGROUND = (200, 195, 185)


def synthetic_frame(
    objects: list[tuple[tuple[int, int, int], tuple[int, int, int, int]]],
    size: tuple[int, int] = (640, 480),
    fmt: str = "JPEG",
    brightness: float = 1.0,
) -> bytes:
    """Paint (colour, (x1, y1, x2, y2)) rectangles on a plain background, in order (later on top)."""
    img = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(img)
    for colour, box in objects:
        draw.rectangle(box, fill=colour)
    if brightness != 1.0:
        arr = np.clip(np.asarray(img, dtype=np.float32) * brightness, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    return buf.getvalue()


def _box_of(mask: np.ndarray) -> Box:
    ys, xs = np.nonzero(mask)
    return Box(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


class MockSegmenter:
    def __init__(self) -> None:
        self.calls: list[str] = []  # concepts requested, for tests

    async def segment(self, jpeg: bytes, concept: str, *, purpose: str = "segment_subject") -> list[Instance]:
        self.calls.append(concept)
        colours = PALETTE.get(" ".join(concept.lower().split()))
        if not colours:
            return []  # like SAM: an unknown concept returns no matches
        rgb = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"), dtype=np.int16)
        instances = []
        for colour in colours:
            mask = np.all(np.abs(rgb - np.array(colour, dtype=np.int16)) <= TOLERANCE, axis=-1)
            if int(mask.sum()) >= MIN_PIXELS:
                instances.append(Instance(box=_box_of(mask), mask=mask, confidence=0.9))
        return instances
