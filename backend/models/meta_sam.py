"""SAM 3.1 through the Responses API (PLAN.md §7.1; test calls 4-8).

One concept per request. The output is special-token text:
    <0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=..;y=..;c=..;data=H,W,~payload|>,1<|box...
Box corners are inclusive source pixels; a mask is box-local (x/y offsets from the box corner).

Records are read with a small tokenizer here and each mask is decoded once with Meta's
`decode_mask_to_raster`. (The official stream parser also decodes every mask to validate it, which
doubled the cost: ~1.2 s of pure-Python decoding for a sofa-sized mask.) Payloads can't contain
`< > | ; , " \\`, so the delimiters are unambiguous; tests check the result against the official
parser. Decoding runs in a worker thread so the event loop keeps serving frames.
"""
from __future__ import annotations

import asyncio
import base64
import io
import re

import httpx
import numpy as np
from meta_sam_parser import SegmentationMask, decode_mask_to_raster
from PIL import Image

from backend.budget import Budget, Usage
from backend.config import Settings
from backend.models.base import Box, Instance
from backend.models.meta_common import make_client, with_retries
from backend.recorder import Recorder

SAM_TIMEOUT_SEC = 20.0

_TOKEN = re.compile(r"<\|(box|mask);([^|<>]*)\|>")
_OBJECT_ID = re.compile(r"(?:^|,)\s*(?:<\d+f>)?\s*([^,<|>\s]+)\s*$")
_ENCODINGS = {"~": "lossless", "!": "one_bit"}


class SamOutputError(ValueError):
    pass


def frame_size(jpeg: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(jpeg)) as im:
        return im.size


def _fields(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            out[key.strip()] = value
    return out


def instances_from_output(raw_output: str, width: int, height: int) -> list[Instance]:
    """SAM output text -> Instances with frame-sized masks. Used for live calls and recorded fixtures."""
    boxes: dict[str, dict[str, str]] = {}
    masks: list[tuple[str, dict[str, str]]] = []
    position = 0
    current_id: str | None = None
    for m in _TOKEN.finditer(raw_output):
        between = raw_output[position:m.start()]
        if id_match := _OBJECT_ID.search(between):
            current_id = id_match.group(1)
        position = m.end()
        if current_id is None:
            raise SamOutputError("record without an object id")
        if m.group(1) == "box":
            boxes[current_id] = _fields(m.group(2))
        else:
            masks.append((current_id, _fields(m.group(2))))

    out: list[Instance] = []
    for object_id, f in masks:
        try:
            h_str, w_str, payload = f["data"].split(",", 2)
            mh, mw = int(h_str), int(w_str)
            encoding = _ENCODINGS[payload[:1]]
        except (KeyError, ValueError) as exc:
            raise SamOutputError(f"bad mask record for object {object_id}") from exc
        local = np.frombuffer(decode_mask_to_raster(SegmentationMask(encoding, payload, mw, mh)),
                              dtype=np.uint8).reshape(mh, mw).astype(bool)
        b = boxes.get(object_id, {})
        left = int(float(b.get("x1", 0))) + int(float(f.get("x", 0)))
        top = int(float(b.get("y1", 0))) + int(float(f.get("y", 0)))
        mask = np.zeros((height, width), dtype=bool)
        y0, x0 = max(top, 0), max(left, 0)
        y1, x1 = min(top + mh, height), min(left + mw, width)
        if y1 <= y0 or x1 <= x0:
            continue
        mask[y0:y1, x0:x1] = local[y0 - top:y1 - top, x0 - left:x1 - left]
        if not mask.any():
            continue
        if b:
            box = Box(int(float(b["x1"])), int(float(b["y1"])), int(float(b["x2"])), int(float(b["y2"])))
        else:
            ys, xs = np.nonzero(mask)
            box = Box(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        conf = f.get("c") or b.get("c")
        out.append(Instance(box=box, mask=mask, confidence=float(conf) if conf else None))
    return out


async def collect_output_text(stream) -> str:
    """Read a Responses stream (SDK event objects or plain dicts) to its final output text."""
    deltas: list[str] = []
    final: str | None = None
    status: str | None = None
    async for event in stream:
        get = event.get if isinstance(event, dict) else (lambda k, e=event: getattr(e, k, None))
        kind = get("type")
        if kind == "response.output_text.delta":
            deltas.append(get("delta") or "")
        elif kind == "response.output_text.done":
            final = get("text")
        elif kind == "response.content_part.done":
            part = get("part")
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text is not None:
                final = text
        elif kind in ("response.completed", "response.failed", "response.incomplete"):
            status = kind.rsplit(".", 1)[1]
        elif kind == "error":
            raise SamOutputError(f"SAM stream error: {get('message') or get('code')}")
    if status != "completed":
        raise SamOutputError(f"SAM response not completed ({status or 'stream ended early'})")
    return final if final is not None else "".join(deltas)


class MetaSegmenter:
    def __init__(self, settings: Settings, budget: Budget, *, recorder: Recorder | None = None,
                 http_client: httpx.AsyncClient | None = None, client=None) -> None:
        self.model = settings.sam_model
        self.budget = budget
        self.recorder = recorder
        self.client = client or make_client(settings, http_client)

    async def segment(self, jpeg: bytes, concept: str, *, purpose: str = "segment_subject") -> list[Instance]:
        width, height = frame_size(jpeg)
        concept = " ".join(concept.split())
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()

        async def attempt() -> str:
            async with self.budget.guard(self.model, purpose, Usage(images=1)) as call:
                stream = await self.client.responses.create(
                    model=self.model, stream=True, timeout=SAM_TIMEOUT_SEC,
                    input=[{"role": "user", "content": [{"type": "input_text", "text": concept},
                                                        {"type": "input_image", "image_url": data_url}]}],
                    metadata={"include_confidence": "true", "mask_encoding": "lossless"},
                )
                call.usage = Usage(images=1)  # billed per image once the request is accepted
                return await collect_output_text(stream)

        raw = await with_retries(attempt)
        if self.recorder:
            self.recorder.sam(jpeg, concept, raw)
        return await asyncio.to_thread(instances_from_output, raw, width, height)
