"""End to end with mocks: rule set in chat -> frames -> mock SAM -> engine -> alert -> claim."""
import json

import pytest

from backend.alerts import Alerts, frame_statements
from backend.budget import Budget
from backend.config import FamilyMember
from backend.engine import Phase
from backend.household import ensure_household
from backend.messaging.base import Inbound
from backend.messaging.mock import MockMessenger
from backend.models import build_adapters
from backend.models.mock_sam import PALETTE, synthetic_frame
from backend.router import Router
from backend.vision import FrameBuffer, Zone, colfill, is_in, load_frame
from backend.watcher import Watcher

BED, PERSON = PALETTE["bed"][0], PALETTE["person"][0]
BED_BOX = (300, 250, 620, 420)
IN_BED = [(BED, BED_BOX), (PERSON, (340, 280, 560, 370))]  # lying on it: bed visible above and below
EMPTY = [(BED, BED_BOX)]
WALKING = [(BED, BED_BOX), (PERSON, (40, 100, 140, 400))]
RULE = "Tell me if Papa gets up from bed and isn't back in 1 minute"


class World:
    def __init__(self, settings, storage, clock):
        settings.family_members_json = [
            FamilyMember(name="Priya", address="1", channel="mock", is_admin=True),
            FamilyMember(name="Rahul", address="2", channel="mock"),
        ]
        self.clock, self.storage, self.settings = clock, storage, settings
        hid = ensure_household(storage, settings)
        self.priya, self.rahul = storage.members(hid)
        adapters = build_adapters(settings, Budget(storage, settings, clock))
        self.messenger = MockMessenger()
        self.frames = FrameBuffer()
        self.router = Router(storage=storage, settings=settings, clock=clock, adapters=adapters,
                             messenger=self.messenger, household_id=hid, frames=self.frames)
        self.alerts = Alerts(storage=storage, settings=settings, clock=clock, adapters=adapters,
                             send=self.router.send, household_id=hid)
        self.watcher = Watcher(storage=storage, settings=settings, clock=clock, adapters=adapters,
                               frames=self.frames, household_id=hid, on_event=self.alerts.on_event)
        self.router.watcher, self.router.alerts = self.watcher, self.alerts
        self._ids = 0

    async def say(self, member, text=None, button=None):
        self._ids += 1
        kind = "button" if button else "text"
        await self.router.handle(Inbound(f"m{self._ids}", "mock", member.address, kind, self.clock.now(),
                                         text=text, button_payload=button))

    async def see(self, objects, seconds=5.0):
        self.clock.advance(seconds)
        frame = load_frame(synthetic_frame(objects), self.clock.now())
        self.frames.add(frame)
        for task in await self.watcher.on_frame(frame):
            await task

    def sent(self, member):
        return self.messenger.to(member.id)


@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


async def arm(world: World) -> int:
    await world.see(IN_BED)
    await world.say(world.priya, RULE)
    rule_id = world.storage.query_one("SELECT id FROM rules")["id"]
    await world.say(world.priya, button=f"yes:{rule_id}")
    assert world.storage.query_one("SELECT status FROM rules WHERE id = ?", (rule_id,))["status"] == "armed"
    return rule_id


async def test_leaving_the_bed_alerts_once_and_a_claim_closes_it(world):
    rule_id = await arm(world)
    rt = world.watcher.runtimes[rule_id]
    for _ in range(4):
        await world.see(IN_BED)
    assert rt.engine.phase == Phase.WATCHING and rt.engine.baseline == 1

    await world.see(WALKING)
    for _ in range(20):  # 100 s away from the bed, rule duration is 60 s
        await world.see(EMPTY)
    alerts = [s for s in world.sent(world.priya) if s.buttons and s.buttons[0].id.startswith("claim:")]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.kind == "image" and alert.text.startswith("⚠️ Papa") and "1 minute" in alert.text
    assert rt.engine.phase == Phase.FIRED

    event = world.storage.query_one("SELECT facts_json, verification_json FROM events")
    assert json.loads(event["facts_json"])["condition"] == "ABSENT_FROM_ZONE"
    for task in list(world.alerts._tasks):
        await task
    assert json.loads(world.storage.query_one("SELECT verification_json FROM events")["verification_json"])["consistent"]

    alert_id = int(alert.buttons[0].id.split(":")[1])
    await world.say(world.priya, button=f"claim:{alert_id}")
    assert world.sent(world.priya)[-1].text == "Thanks, I've told the others."
    assert world.storage.query_one("SELECT status, claimed_by FROM alerts")["status"] == "claimed"
    await world.say(world.priya, button=f"claim:{alert_id}")  # second tap
    assert world.sent(world.priya)[-1].text == "Priya is already handling it."

    for _ in range(4):
        await world.see(IN_BED)
    assert rt.engine.phase == Phase.WATCHING  # occurrence over, nothing re-fires
    timeline = [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    assert {"rule_pending", "rule_fired", "alert_sent", "alert_claimed", "rule_over"} <= set(timeline)


async def test_short_absence_is_quiet_and_sam_is_paced(world):
    rule_id = await arm(world)
    calls_before = len(world.router.adapters.segmenter.calls)
    for objects in [IN_BED] * 4 + [EMPTY] * 6 + [IN_BED] * 4:
        await world.see(objects)
    assert not any(s.buttons and s.buttons[0].id.startswith("claim:") for s in world.sent(world.priya))
    assert "rule_returned" in [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    # 14 frames over 70 s, but identical frames don't trigger SAM between heartbeats
    assert len(world.router.adapters.segmenter.calls) - calls_before < 14


async def test_dark_frames_are_unknown_not_empty(world):
    rule_id = await arm(world)
    for _ in range(4):
        await world.see(IN_BED)
    rt = world.watcher.runtimes[rule_id]
    world.clock.advance(5)
    dark = load_frame(synthetic_frame(EMPTY, brightness=0.05), world.clock.now())
    for _ in range(10):
        world.clock.advance(5)
        dark = load_frame(synthetic_frame(EMPTY, brightness=0.05), world.clock.now())
        for task in await world.watcher.on_frame(dark):
            await task
    assert rt.engine.phase == Phase.WATCHING  # never "he left" from a dark room


async def test_armed_rules_and_timers_survive_a_restart(world, settings, storage, clock):
    rule_id = await arm(world)
    for objects in [IN_BED] * 4 + [EMPTY] * 4:
        await world.see(objects)
    since = world.watcher.runtimes[rule_id].engine.since
    assert world.watcher.runtimes[rule_id].engine.phase == Phase.PENDING
    fresh = Watcher(storage=storage, settings=settings, clock=clock, adapters=world.router.adapters,
                    frames=FrameBuffer(), household_id=world.router.household_id)
    fresh.reload()
    assert fresh.runtimes[rule_id].engine.since == since
    assert fresh.runtimes[rule_id].zone is not None


def test_lying_person_counts_as_in_the_bed_thanks_to_colfill():
    import numpy as np

    bed = np.zeros((480, 640), bool)
    bed[250:420, 300:620] = True
    bed[285:365, 340:560] = False  # SAM leaves out the part hidden by the person lying on it
    person = np.zeros((480, 640), bool)
    person[285:365, 340:560] = True
    zone = Zone.from_mask(bed, "bed")
    assert is_in(person, zone) and colfill(bed)[300, 400]
    assert not is_in(person, Zone(mask=bed, area=bed, box=zone.box))  # without colfill: 'not in bed'


def test_statements_describe_each_frame():
    from backend.schemas import Condition, Rule, Subject

    rule = Rule(household_id=1, created_by=1, original_text="x", subject=Subject.PERSON, zone_object="bed",
                condition=Condition.ABSENT_FROM_ZONE, duration_sec=600, notify=[1], language="hi")
    assert frame_statements(rule, 3) == ["a person is on or in the bed", "no person is on or in the bed",
                                         "no person is on or in the bed"]
