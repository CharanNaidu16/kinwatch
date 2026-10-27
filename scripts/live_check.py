"""One real call to each Meta model: checks your key and records fixtures. Costs about $0.02.

    python scripts/live_check.py --spend --text "पापा अगर रात को बिस्तर से उठें ..." \
        --image photo.jpg --zone bed --voice note.ogg

Each part is optional. Calls go through the budget guard (counted in data/kinwatch.db) and their
responses are saved under fixtures/recorded/ with family names replaced by {MEMBER:n}.
The API key is never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageOps  # noqa: E402

from backend.budget import Budget  # noqa: E402
from backend.clock import SystemClock  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.media import voice_to_wav  # noqa: E402
from backend.models.meta_sam import MetaSegmenter  # noqa: E402
from backend.models.meta_spark import MetaSpark  # noqa: E402
from backend.models.meta_transcribe import MetaTranscriber  # noqa: E402
from backend.recorder import Recorder  # noqa: E402
from backend.storage import Storage  # noqa: E402

SAMPLE_HOUSEHOLD = ["Priya", "Rahul"]  # used only when FAMILY_MEMBERS_JSON is empty


def to_frame(path: Path) -> bytes:
    """Downscale like the phone page does: 640 px on the long side, JPEG quality 70."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((640, 640))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--spend", action="store_true", help="required: confirms you want real, billed calls")
    ap.add_argument("--text")
    ap.add_argument("--image", type=Path)
    ap.add_argument("--zone", default="bed")
    ap.add_argument("--voice", type=Path)
    ap.add_argument("--language", default="Hindi")
    args = ap.parse_args()
    if not args.spend:
        sys.exit("Refusing to make billed calls without --spend.")

    settings = Settings()
    storage = Storage(settings.db_path)
    storage.migrate()
    budget = Budget(storage, settings, SystemClock())
    names = [[m.name, *m.aliases] for m in settings.family_members_json] or [[n] for n in SAMPLE_HOUSEHOLD]
    household = [n[0] for n in names]
    recorder = Recorder(names)

    if args.voice:
        wav = voice_to_wav(args.voice.read_bytes())
        t = time.perf_counter()
        tr = await MetaTranscriber(settings, budget, recorder=recorder).transcribe(wav, [args.language])
        print(f"[transcribe] {int((time.perf_counter() - t) * 1000)} ms, {tr.duration_ms} ms audio")
        print(f"  transcript: {tr.text}")
        args.text = args.text or tr.text

    if args.text:
        spark = MetaSpark(settings, budget, recorder=recorder)
        rule = await spark.parse_rule(args.text, household, household[0])
        print(f"[parse_rule] {spark.last_latency_ms} ms")
        print(f"  {rule.model_dump_json()}")

    if args.image:
        jpeg = to_frame(args.image)
        concept = settings.sam_concept(args.zone)
        t = time.perf_counter()
        found = await MetaSegmenter(settings, budget, recorder=recorder).segment(jpeg, concept, purpose="live_check")
        print(f"[segment] '{args.zone}' -> SAM concept '{concept}': {len(found)} instance(s), "
              f"{int((time.perf_counter() - t) * 1000)} ms")
        for i, inst in enumerate(found, 1):
            print(f"  {i}: box {inst.box}, confidence {inst.confidence:.2f}, {int(inst.mask.sum())} px")

    s = budget.summary()
    print(f"[budget] total ${s['total_usd']:.4f}, today ${s['today_usd']:.4f}")
    storage.close()


if __name__ == "__main__":
    asyncio.run(main())
