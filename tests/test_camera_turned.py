"""The phone is turned: the zone is found again on the new picture, or the family is told it can't be."""
import numpy as np
import pytest

from backend.engine import Phase
from backend.models.base import Box, Instance
from backend.models.mock_sam import synthetic_frame
from backend.vision import load_frame
from backend.watcher import match_zone
from tests.test_watcher import BED, EMPTY, IN_BED, PERSON, World, arm

# The same room after a quarter turn: 640x480 becomes 480x640 and the bed moves with it.
TURNED_IN_BED = [(BED, (60, 300, 230, 620)), (PERSON, (90, 340, 200, 560))]
TURNED_EMPTY = [(BED, (60, 300, 230, 620))]


@pytest.fixture
def world(settings, storage, clock):
    w = World(settings, storage, clock)
    w.notices = []

    async def on_zone(rule, kind, photo):
        w.notices.append((rule.id, kind, photo[:2]))
    w.watcher.on_zone = on_zone
    return w


async def see_turned(world, objects, seconds=5.0):
    world.clock.advance(seconds)
    frame = load_frame(synthetic_frame(objects, size=(480, 640)), world.clock.now())
    world.frames.add(frame)
    for task in await world.watcher.on_frame(frame):
        await task


def inst(x1, y1, x2, y2, shape) -> Instance:
    mask = np.zeros(shape, bool)
    mask[y1:y2, x1:x2] = True
    return Instance(Box(x1, y1, x2, y2), mask)


def test_match_zone_follows_a_quarter_turn():
    # Old: the bed in the right half of a landscape picture. After turning, two beds are seen.
    old_box, old_shape, new_shape = (400, 200, 620, 400), (480, 640), (640, 480)
    top, bottom = inst(100, 20, 380, 200, new_shape), inst(100, 440, 380, 620, new_shape)
    picked = match_zone([top, bottom], old_box, old_shape, new_shape)
    assert picked in (top, bottom)
    # Unturned picture of the same shape: the one in the same place wins.
    same = inst(400, 200, 620, 400, old_shape)
    other = inst(10, 10, 100, 100, old_shape)
    assert match_zone([other, same], old_box, old_shape, old_shape) is same


async def test_turning_the_phone_finds_the_bed_again_and_keeps_watching(world):
    rule_id = await arm(world)
    rt = world.watcher.runtimes[rule_id]
    for _ in range(4):
        await world.see(IN_BED)
    assert rt.zone.shape == (480, 640)

    await see_turned(world, TURNED_IN_BED)
    assert rt.zone.shape == (640, 480)  # found again on the turned picture
    assert world.notices == [(rule_id, "found", bytes([0xFF, 0xD8]))]
    kinds = [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    assert "zone_refreshed" in kinds

    # Watching carries on in the new orientation: leaving the bed still leads to an alert.
    for objects in [TURNED_IN_BED] * 3 + [TURNED_EMPTY] * 20:
        await see_turned(world, objects)
    assert rt.engine.phase == Phase.FIRED
    assert world.storage.query_one("SELECT COUNT(*) AS n FROM alerts")["n"] == 1


async def test_turned_away_from_the_bed_is_reported_once_then_retried(world):
    rule_id = await arm(world)
    for _ in range(3):
        await world.see(IN_BED)
    sam = world.router.adapters.segmenter
    await see_turned(world, [(PERSON, (90, 340, 200, 560))])  # no bed in view
    await see_turned(world, [(PERSON, (90, 340, 200, 560))])  # within the retry gap: no new SAM call for the zone
    assert world.notices == [(rule_id, "missing", bytes([0xFF, 0xD8]))]
    zone_calls = sam.calls.count("bed")
    world.clock.advance(31)
    await see_turned(world, TURNED_IN_BED)  # pointed back at the bed
    assert sam.calls.count("bed") > zone_calls  # looked again
    assert world.notices[-1][:2] == (rule_id, "found")


async def test_a_dark_turned_frame_waits_for_light(world):
    rule_id = await arm(world)
    for _ in range(3):
        await world.see(IN_BED)
    world.clock.advance(5)
    dark = load_frame(synthetic_frame(TURNED_EMPTY, size=(480, 640), brightness=0.05), world.clock.now())
    for task in await world.watcher.on_frame(dark):
        await task
    assert world.notices == [] and world.watcher.runtimes[rule_id].zone.shape == (480, 640)


async def test_the_new_zone_survives_a_restart(world, settings, storage, clock):
    from backend.vision import FrameBuffer
    from backend.watcher import Watcher

    rule_id = await arm(world)
    await world.see(IN_BED)
    await see_turned(world, TURNED_IN_BED)
    fresh = Watcher(storage=storage, settings=settings, clock=clock, adapters=world.router.adapters,
                    frames=FrameBuffer(), household_id=world.router.household_id)
    fresh.reload()
    assert fresh.runtimes[rule_id].zone.shape == (640, 480)


def test_turned_frames_look_right():
    turned = load_frame(synthetic_frame(TURNED_EMPTY, size=(480, 640)), 0.0)
    assert load_frame(synthetic_frame(EMPTY), 0.0).shape != turned.shape
