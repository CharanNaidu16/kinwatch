"""Real adapters against fake transports: request shape, parsing, budget accounting, retry policy."""
import io
import json

import httpx
import numpy as np
import pytest

from backend.budget import Budget
from backend.config import Settings
from backend.models import meta_common
from backend.models.meta_sam import MetaSegmenter, instances_from_output
from backend.models.meta_spark import MetaSpark
from backend.models.meta_transcribe import MetaTranscriber
from backend.models.mock_sam import MockSegmenter, synthetic_frame
from backend.models.mock_spark import MockSpark
from backend.recorder import RECORDED, Recorder, frame_key
from backend.schemas import Condition

SOFA_FIXTURE = next((RECORDED / "sam").glob("*_sofa.txt"))
RULE = {"status": "ok", "clarifying_question": None, "subject": "person", "subject_name": "पापा",
        "zone_object": "bed", "condition": "ABSENT_FROM_ZONE", "duration_sec": 600,
        "time_window": {"start": "22:00", "end": "06:00"}, "guard": None, "notify": ["Asha"], "language": "hi"}


@pytest.fixture
def live_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, use_mocks=False, meta_api_key="sk-test", data_dir=tmp_path / "data")


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(meta_common, "RETRY_DELAYS_SEC", (0.0, 0.0))


def _completion(content: dict) -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 1700, "total_tokens": 2400,
                      "prompt_tokens_details": {"cached_tokens": 100}}}


def _calls(storage) -> list:
    return storage.query("SELECT model, purpose, ok, error_code, est_cost_usd, mode FROM api_calls ORDER BY id")


async def test_parse_rule_request_shape_and_budget(live_settings, storage, clock):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=_completion(RULE))

    budget = Budget(storage, live_settings, clock)
    spark = MetaSpark(live_settings, budget, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rule = await spark.parse_rule("पापा अगर रात को उठें ...", ["Asha", "Vikram"], "Asha")

    assert rule.condition == Condition.ABSENT_FROM_ZONE and rule.notify == ["Asha"]
    body = seen[0]
    assert body["model"] == "muse-spark-1.3" and body["reasoning_effort"] == "low"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "kinwatch_rule"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1]["content"].startswith("HOUSEHOLD: Asha, Vikram\nSENDER: Asha\nMESSAGE:")
    [call] = _calls(storage)
    assert (call["model"], call["purpose"], call["ok"], call["mode"]) == ("muse-spark-1.3", "parse_rule", 1, "live")
    # 600 uncached + 100 cached input, 1700 output tokens
    assert call["est_cost_usd"] == pytest.approx(600 * 1.25e-6 + 100 * 0.15e-6 + 1700 * 4.25e-6)


async def test_spark_retries_5xx_but_not_4xx(live_settings, storage, clock):
    answers = [httpx.Response(503, json={"error": "busy"}), httpx.Response(200, json=_completion(RULE))]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: answers.pop(0)))
    spark = MetaSpark(live_settings, Budget(storage, live_settings, clock), http_client=client)
    await spark.parse_rule("x", ["Asha"], "Asha")
    assert [c["ok"] for c in _calls(storage)] == [0, 1]  # every attempt is counted

    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "no"})))
    spark = MetaSpark(live_settings, Budget(storage, live_settings, clock), http_client=bad)
    with pytest.raises(Exception):
        await spark.parse_rule("x", ["Asha"], "Asha")
    assert [c["error_code"] for c in _calls(storage)][-1] == "400"
    assert len(_calls(storage)) == 3  # the 400 was not retried


async def test_confirm_event_sends_one_statement_per_frame(live_settings, storage, clock):
    from backend.models.base import TimedFrame

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(
            {"consistent": True, "visible_summary": "person, then empty sofa", "confidence": "high"}))

    spark = MetaSpark(live_settings, Budget(storage, live_settings, clock),
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    frames = [TimedFrame(ts=clock.now() + i * 300, jpeg=synthetic_frame([])) for i in range(3)]
    v = await spark.confirm_event(frames, ["a person is on the bed", "no person is on the bed", "no person is on the bed"])
    assert v.consistent and v.confidence == "high"
    content = seen[0]["messages"][1]["content"]
    assert content[0]["text"].splitlines()[0] == "Frame 1 (21:00): a person is on the bed"
    assert [p["type"] for p in content[1:]] == ["image_url"] * 3
    assert seen[0]["reasoning_effort"] == "minimal"
    assert _calls(storage)[0]["purpose"] == "confirm_event"


# --- SAM ------------------------------------------------------------------------------------------

def test_instances_from_recorded_sofa_output():
    [sofa] = instances_from_output(SOFA_FIXTURE.read_text(encoding="utf-8"), 640, 480)
    assert sofa.mask.shape == (480, 640) and sofa.mask.dtype == bool
    assert (sofa.box.x1, sofa.box.y1, sofa.box.x2, sofa.box.y2) == (231, 189, 639, 478)
    ys, xs = np.nonzero(sofa.mask)
    assert xs.min() >= sofa.box.x1 and xs.max() <= sofa.box.x2 and ys.min() >= sofa.box.y1 and ys.max() <= sofa.box.y2
    assert sofa.confidence == pytest.approx(0.883, abs=0.001)


def test_no_match_is_an_empty_list():
    assert instances_from_output("", 640, 480) == []


def test_tokenizer_matches_metas_official_parser():
    """Our single-decode reader must agree with meta_sam_parser on boxes, masks and confidence."""
    from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, image_segmentation_format

    raw = SOFA_FIXTURE.read_text(encoding="utf-8")
    parser = image_segmentation_format().create_parser()
    parser.push(raw)
    records = parser.finish(CompletedOutcome()).result.records
    official = [r for r in records if r.kind == "mask"]
    ours = instances_from_output(raw, 640, 480)
    assert len(ours) == len(official) == 1
    rec, inst = official[0], ours[0]
    local = np.frombuffer(decode_mask_to_raster(rec.mask), np.uint8).reshape(rec.mask.height, rec.mask.width)
    top, left = round(rec.bounds.top), round(rec.bounds.left)
    expected = np.zeros((480, 640), bool)
    expected[top:top + local.shape[0], left:left + local.shape[1]] = local.astype(bool)
    assert np.array_equal(inst.mask, expected)
    assert inst.confidence == pytest.approx(rec.confidence)


def test_two_instances_and_ids_are_read():
    from backend.models.meta_sam import SamOutputError

    one = SOFA_FIXTURE.read_text(encoding="utf-8").strip()
    record = one[one.index("0<|box"):] if one.startswith("<0f>") else one
    two = "<0f>" + record + "," + "1" + record[1:]
    assert len(instances_from_output(two, 640, 480)) == 2
    with pytest.raises(SamOutputError):
        instances_from_output("<|mask;x=0;y=0;data=1,1,~xx|>", 640, 480)


async def test_incomplete_stream_is_an_error():
    from backend.models.meta_sam import SamOutputError, collect_output_text

    async def events():
        yield {"type": "response.output_text.delta", "delta": "<0f>"}

    with pytest.raises(SamOutputError, match="not completed"):
        await collect_output_text(events())


class _FakeResponses:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        raw = self.raw

        async def events():
            ids = {"item_id": "m1", "output_index": 0, "content_index": 0}
            yield {"type": "response.created", "sequence_number": 0, "response": {"id": "r1", "status": "in_progress"}}
            yield {"type": "response.output_text.delta", "sequence_number": 1, "delta": raw, **ids}
            yield {"type": "response.content_part.done", "sequence_number": 2,
                   "part": {"type": "output_text", "text": raw}, **ids}
            yield {"type": "response.completed", "sequence_number": 3, "response": {"id": "r1", "status": "completed"}}
        return events()


class _FakeClient:
    def __init__(self, raw: str) -> None:
        self.responses = _FakeResponses(raw)


async def test_segmenter_streams_parses_records_and_counts_an_image(live_settings, storage, clock, tmp_path):
    raw = SOFA_FIXTURE.read_text(encoding="utf-8")
    client = _FakeClient(raw)
    recorder = Recorder([["Asha"]], root=tmp_path)
    sam = MetaSegmenter(live_settings, Budget(storage, live_settings, clock), recorder=recorder, client=client)
    jpeg = synthetic_frame([])
    [sofa] = await sam.segment(jpeg, "  sofa ", purpose="segment_zone")

    assert sofa.mask.sum() > 20000
    req = client.responses.requests[0]
    assert req["model"] == "sam-3.1" and req["stream"] is True
    assert req["input"][0]["content"][0] == {"type": "input_text", "text": "sofa"}
    assert req["metadata"] == {"include_confidence": "true", "mask_encoding": "lossless"}
    [call] = _calls(storage)
    assert call["purpose"] == "segment_zone" and call["est_cost_usd"] == pytest.approx(0.0025)
    # recorded, and served back by the mock
    assert (tmp_path / "sam" / f"{frame_key(jpeg, 'sofa')}.txt").read_text(encoding="utf-8") == raw
    [again] = await MockSegmenter(recorded=tmp_path).segment(jpeg, "sofa")
    assert np.array_equal(again.mask, sofa.mask)


# --- Voice Transcribe ---------------------------------------------------------------------------

def _wav(seconds: float, rate: int = 16000) -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


async def test_transcribe_multipart_and_budget(live_settings, storage, clock, tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"sessionId": "s", "transcript": " पापा अगर  रात को उठें ",
                                         "audioDurationMs": 9840, "turns": []})

    recorder = Recorder([["Asha", "आशा"]], root=tmp_path)
    tr = MetaTranscriber(live_settings, Budget(storage, live_settings, clock), recorder=recorder,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    wav = _wav(9.84)
    t = await tr.transcribe(wav, ["Hindi"])

    assert t.text == "पापा अगर रात को उठें" and t.duration_ms == 9840
    assert seen["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert b'"audioEncoding": "WAV"' in seen["body"] and b'"languageBias": ["Hindi"]' in seen["body"]
    assert b'name="audio"; filename="voice.wav"' in seen["body"]
    [call] = _calls(storage)
    assert call["est_cost_usd"] == pytest.approx(9.84 * 0.18 / 3600)


async def test_transcribe_error_is_raised_with_status(live_settings, storage, clock):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"message": "You must specify the 'audioEncoding' field"}})))
    tr = MetaTranscriber(live_settings, Budget(storage, live_settings, clock), http_client=client)
    with pytest.raises(meta_common.MetaAPIError) as err:
        await tr.transcribe(_wav(1), ["Hindi"])
    assert err.value.status_code == 400
    assert len(_calls(storage)) == 1


def test_real_adapters_need_a_key(tmp_path, storage, clock):
    s = Settings(_env_file=None, use_mocks=False, data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="META_API_KEY"):
        MetaSpark(s, Budget(storage, s, clock))
    with pytest.raises(RuntimeError, match="META_API_KEY"):
        MetaTranscriber(s, Budget(storage, s, clock))


async def test_mock_spark_serves_the_recorded_parse_with_this_households_names():
    rule = await MockSpark().parse_rule("पापा अगर रात को उठे और दस मिनट में वापस नहीं आए तो मुझे बताना।",
                                        ["Asha", "Vikram"], "Asha")
    assert rule.notify == ["Asha"] and rule.duration_sec == 600 and rule.guard is None
