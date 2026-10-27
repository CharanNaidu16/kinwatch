"""Meta model adapters. Real implementations live in meta_*.py, mocks in mock_*.py (PLAN.md §7.2)."""
from __future__ import annotations

from dataclasses import dataclass

from backend.budget import Budget
from backend.config import Settings
from backend.models.base import Segmenter, Spark, Transcriber
from backend.recorder import Recorder


@dataclass
class Adapters:
    spark: Spark
    segmenter: Segmenter
    transcriber: Transcriber
    live: bool


def member_names(settings: Settings) -> list[list[str]]:
    """[name, *aliases] per member, for scrubbing names out of recorded fixtures."""
    return [[m.name, *m.aliases] for m in settings.family_members_json]


def build_adapters(settings: Settings, budget: Budget) -> Adapters:
    """USE_MOCKS=true (the default) and MODE=replay never construct a real adapter."""
    if settings.use_mocks or settings.mode == "replay":
        from backend.models.mock_sam import MockSegmenter
        from backend.models.mock_spark import MockSpark
        from backend.models.mock_transcribe import MockTranscriber

        return Adapters(spark=MockSpark(), segmenter=MockSegmenter(), transcriber=MockTranscriber(), live=False)

    from backend.models.meta_sam import MetaSegmenter
    from backend.models.meta_spark import MetaSpark
    from backend.models.meta_transcribe import MetaTranscriber

    recorder = Recorder(member_names(settings)) if settings.record_fixtures else None
    return Adapters(
        spark=MetaSpark(settings, budget, recorder=recorder),
        segmenter=MetaSegmenter(settings, budget, recorder=recorder),
        transcriber=MetaTranscriber(settings, budget, recorder=recorder),
        live=True,
    )
