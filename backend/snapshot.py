"""Confirmation and alert images: SAM outlines drawn on the camera frame (PLAN.md §3.3).

Zones get thick green outlines with big numbered badges, so the family can answer "2" or
"the other one"; subjects are outlined in blue.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.models.base import InstanceDesc
from backend.vision import colfill

GREEN = (40, 220, 90)
BLUE = (60, 140, 255)
OUTLINE_PX = 3


def _edge(mask: np.ndarray, px: int = OUTLINE_PX) -> np.ndarray:
    inner = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(2 * px + 1))) > 0
    return mask & ~inner


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def draw(jpeg: bytes, zones: list[np.ndarray], *, numbered: bool, subjects: list[np.ndarray] | None = None,
         quality: int = 85) -> bytes:
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    pixels = np.array(img)
    for mask in subjects or []:
        pixels[_edge(mask, 2)] = BLUE
    for mask in zones:
        # Outline the filled zone: what the engine tests against, and a cleaner shape than SAM's mask,
        # which leaves out whatever (or whoever) covers the object.
        pixels[_edge(colfill(mask))] = GREEN
    img = Image.fromarray(pixels)
    if numbered:
        d = ImageDraw.Draw(img)
        size = max(18, img.width // 20)
        font = _font(size)
        for n, mask in enumerate(zones, 1):
            ys, xs = np.nonzero(mask)
            if len(xs) == 0:
                continue
            cx, cy = int(xs.mean()), int(ys.mean())
            r = int(size * 0.8)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GREEN, outline=(255, 255, 255), width=3)
            d.text((cx, cy), str(n), fill=(0, 0, 0), font=font, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def describe(zones: list[np.ndarray], width: int) -> list[InstanceDesc]:
    """Position and size rank of each numbered zone, for resolving "the other one" / "the left one"."""
    areas = [int(m.sum()) for m in zones]
    ranks = {i: r for r, i in enumerate(sorted(range(len(zones)), key=lambda i: -areas[i]), 1)}
    out = []
    for i, mask in enumerate(zones):
        xs = np.nonzero(mask)[1]
        cx = xs.mean() / width if len(xs) else 0.5
        position = "left" if cx < 0.4 else "right" if cx > 0.6 else "middle"
        out.append(InstanceDesc(n=i + 1, position=position, size_rank=ranks[i]))
    return out
