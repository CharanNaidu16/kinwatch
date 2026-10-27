"""Meta model adapters. Real implementations live in meta_*.py, mocks in mock_*.py (PLAN.md §7.2)."""
from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.models.base import Segmenter, Spark, Transcriber


@dataclass
class Adapters:
    spark: Spark
    segmenter: Segmenter
    transcriber: Transcriber
    live: bool


def build_adapters(settings: Settings) -> Adapters:
    """USE_MOCKS=true (the default) never constructs a real adapter."""
    if settings.use_mocks or settings.mode == "replay":
        from backend.models.mock_sam import MockSegmenter
        from backend.models.mock_spark import MockSpark
        from backend.models.mock_transcribe import MockTranscriber

        return Adapters(spark=MockSpark(), segmenter=MockSegmenter(), transcriber=MockTranscriber(), live=False)
    raise NotImplementedError("real Meta adapters arrive on Wed 28 Oct; set USE_MOCKS=true")
