"""When the alerted situation ends (Papa is back in bed), everyone who got the alert is told."""
import pytest

from backend import templates
from backend.schemas import Condition, Guard, Rule, Subject
from tests.test_escalation import kinds, timeout
from tests.test_watcher import EMPTY, IN_BED, WALKING, World, arm


@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


async def fire(world) -> int:
    await arm(world)  # 1-minute rule, notify: Priya
    for objects in [IN_BED] * 4 + [WALKING] + [EMPTY] * 20:
        await world.see(objects)
    return world.storage.query_one("SELECT id FROM alerts")["id"]


def alert_status(world, alert_id) -> str:
    return world.storage.query_one("SELECT status FROM alerts WHERE id = ?", (alert_id,))["status"]


async def test_back_before_anyone_claimed_stops_the_escalation(world):
    alert_id = await fire(world)
    await timeout(world)  # re-sent to Priya
    for _ in range(4):
        await world.see(IN_BED)  # Papa comes back
    assert alert_status(world, alert_id) == "resolved"
    back = world.sent(world.priya)[-1].text
    assert back.startswith("✅ Papa is back in the bed (") and "after" in back
    sent_before = len(world.messenger.outbox)
    await timeout(world)
    await timeout(world)
    assert len(world.messenger.outbox) == sent_before  # no more re-sends or escalation
    assert "escalation" not in kinds(world, world.rahul)
    timeline = [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    assert "alert_resolved" in timeline


async def test_back_after_a_claim_still_tells_everyone(world):
    alert_id = await fire(world)
    await timeout(world)
    await timeout(world)  # Rahul has it too now
    await world.say(world.rahul, button=f"claim:{alert_id}")
    for _ in range(4):
        await world.see(IN_BED)
    assert alert_status(world, alert_id) == "claimed"  # who handled it stays on record
    assert world.sent(world.priya)[-1].text.startswith("✅ Papa is back in the bed")
    assert world.sent(world.rahul)[-1].text.startswith("✅ Papa is back in the bed")


async def test_a_tap_after_papa_is_back_gets_a_clear_answer(world):
    alert_id = await fire(world)
    for _ in range(4):
        await world.see(IN_BED)
    await world.say(world.priya, button=f"claim:{alert_id}")
    assert world.sent(world.priya)[-1].text == "No need: this alert is already over (see the ✅ message)."
    assert alert_status(world, alert_id) == "resolved"


async def test_the_back_notice_is_sent_once(world):
    await fire(world)
    for _ in range(4):
        await world.see(IN_BED)
    backs = [s for s in world.sent(world.priya) if s.text.startswith("✅ Papa is back")]
    assert len(backs) == 1


async def test_back_notice_comes_with_the_camera_picture(world):
    await fire(world)
    for _ in range(4):
        await world.see(IN_BED)
    back = world.sent(world.priya)[-1]
    assert back.text.startswith("✅ Papa is back")
    assert back.kind == "image" and back.jpeg == world.frames.latest().jpeg  # as the camera took it


async def test_back_notice_in_the_readers_language(world):
    alert_id = await fire(world)
    world.storage.execute("UPDATE members SET last_language = 'hi' WHERE id = ?", (world.priya.id,))
    for _ in range(4):
        await world.see(IN_BED)
    assert world.sent(world.priya)[-1].text.startswith("✅ Papa वापस बिस्तर पर आ गए (")
    assert alert_status(world, alert_id) == "resolved"


def rule(condition, guard=None) -> Rule:
    return Rule(household_id=1, created_by=1, original_text="x", subject=Subject.PERSON, subject_name="Papa",
                zone_object="bed" if condition != Condition.NO_PERSON_NEAR else "gas stove", condition=condition,
                duration_sec=300, notify=[1], language="en", guard=guard)


def test_back_notice_wording():
    assert templates.back_notice(rule(Condition.ABSENT_FROM_ZONE), "20:46", 240, "en") == \
        "✅ Papa is back in the bed (8:46 PM, after 4 minutes)."
    assert templates.back_notice(rule(Condition.PRESENT_IN_ZONE), "20:46", 10, "en") is None  # arrivals: no follow-up
    stove = rule(Condition.NO_PERSON_NEAR, Guard(subject=Subject.FLAME, zone_object="gas stove"))
    assert templates.back_notice(stove, "19:50", 60, "en") == \
        "✅ At 7:50 PM the flame was off or someone was near the gas stove again."
