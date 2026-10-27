import pytest

from backend.models.mock_sam import PALETTE, synthetic_frame
from backend.vision import ChangeDetector, FrameBuffer, FrameError, load_frame

PERSON = PALETTE["person"][0]
BED = PALETTE["bed"][0]


def _frame(objects, ts=0.0, **kw):
    return load_frame(synthetic_frame(objects, **kw), ts)


def test_load_frame_reads_size_and_light():
    f = _frame([(BED, (100, 200, 500, 400))], size=(480, 640))
    assert (f.width, f.height, f.shape) == (480, 640, "480x640")
    assert not f.dark
    assert _frame([], brightness=0.08).dark


@pytest.mark.parametrize("body", [b"", b"not an image", b"GIF89a" + b"\x00" * 50])
def test_bad_frames_are_rejected(body):
    with pytest.raises(FrameError):
        load_frame(body, 0.0)


def test_change_detector_ignores_jpeg_noise_and_sees_a_person():
    det = ChangeDetector()
    empty = _frame([(BED, (100, 200, 500, 400))])
    assert det.changed(empty)  # nothing analysed yet
    det.mark_analysed(empty)
    assert not det.changed(_frame([(BED, (100, 200, 500, 400))]))  # same scene, re-encoded
    assert det.changed(_frame([(BED, (100, 200, 500, 400)), (PERSON, (200, 150, 330, 380))]))
    assert det.changed(_frame([(BED, (100, 200, 500, 400))], size=(480, 640)))  # phone rotated


def test_frame_buffer_keeps_the_last_n_and_knows_staleness():
    buf = FrameBuffer(maxlen=3)
    for ts in range(5):
        buf.add(_frame([], ts=float(ts)))
    assert len(buf) == 3
    assert buf.latest().ts == 4.0
    assert buf.latest(now=20.0, max_age_sec=10) is None
    assert [f.ts for f in buf.since(3.0)] == [3.0, 4.0]
