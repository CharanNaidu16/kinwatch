"""Claim timeouts: re-send, then the next member, then expiry (PLAN.md §3.8). Mocks and a fake clock."""
import asyncio

import pytest

from backend.alerts import MAX_ESCALATIONS, Alerts
from backend.router import is_claim
from tests.test_watcher import EMPTY, IN_BED, WALKING, World, arm


@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


async def fire(world: World) -> int:
    """Arm the bed rule (notify: Priya only) and stay away until the alert goes out."""
    await arm(world)
    for objects in [IN_BED] * 4 + [WALKING] + [EMPTY] * 20:
        await world.see(objects)
    row = world.storage.query_one("SELECT id FROM alerts")
    assert row is not None
    return row["id"]


async def timeout(world: World) -> None:
    world.clock.advance(world.settings.effective_claim_timeout_sec + 1)
    await world.alerts.escalate(world.clock.now())


def kinds(world: World, member) -> list[str]:
    return [r["kind"] for r in world.storage.query(
        "SELECT kind FROM deliveries WHERE member_id = ? AND alert_id IS NOT NULL ORDER BY id", (member.id,))]


def alert_row(world: World, alert_id: int):
    return world.storage.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))


async def test_unclaimed_alert_is_resent_then_escalated_then_expires(world):
    alert_id = await fire(world)
    assert kinds(world, world.priya) == ["alert"] and kinds(world, world.rahul) == []

    await world.alerts.escalate(world.clock.now())  # not due yet
    assert alert_row(world, alert_id)["escalation_level"] == 0

    await timeout(world)  # level 1: re-send to Priya
    assert kinds(world, world.priya) == ["alert", "resend"]
    resent = world.sent(world.priya)[-1]
    assert resent.text.startswith("⏰ Nobody has tapped") and resent.kind == "image"
    assert resent.buttons[0].id == f"claim:{alert_id}"

    await timeout(world)  # level 2: Rahul is brought in
    assert kinds(world, world.rahul) == ["escalation"]
    assert world.sent(world.rahul)[-1].text.startswith("⚠️ Priya didn't answer")

    await timeout(world)  # level 3: nobody left to add, so both get a re-send
    assert kinds(world, world.priya)[-1] == "resend" and kinds(world, world.rahul)[-1] == "resend"

    await timeout(world)  # past MAX_ESCALATIONS: expired, final notice to both
    assert MAX_ESCALATIONS == 3
    row = alert_row(world, alert_id)
    assert row["status"] == "expired" and row["next_escalation_at"] is None
    assert world.sent(world.priya)[-1].text.startswith("Nobody claimed the alert")
    assert world.sent(world.rahul)[-1].text.startswith("Nobody claimed the alert")

    sent_before = len(world.messenger.outbox)
    await timeout(world)  # expired alerts never escalate again
    assert len(world.messenger.outbox) == sent_before

    timeline = [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    assert {"alert_resent", "alert_escalated", "alert_expired"} <= set(timeline)


async def test_a_claim_stops_escalation_and_tells_the_escalated_member(world):
    alert_id = await fire(world)
    await timeout(world)
    await timeout(world)  # Rahul has it now
    await world.say(world.rahul, button=f"claim:{alert_id}")
    assert world.sent(world.rahul)[-1].text == "Thanks, I've told the others."
    assert world.sent(world.priya)[-1].text.startswith("Rahul is handling it")
    row = alert_row(world, alert_id)
    assert row["status"] == "claimed" and row["next_escalation_at"] is None

    sent_before = len(world.messenger.outbox)
    await timeout(world)
    assert len(world.messenger.outbox) == sent_before


async def test_a_late_claim_after_expiry_still_counts(world):
    alert_id = await fire(world)
    for _ in range(MAX_ESCALATIONS + 1):
        await timeout(world)
    assert alert_row(world, alert_id)["status"] == "expired"
    await world.say(world.priya, button=f"claim:{alert_id}")
    assert alert_row(world, alert_id)["status"] == "claimed"
    assert world.sent(world.rahul)[-1].text.startswith("Priya is handling it")


async def test_claim_and_escalation_at_the_same_moment_never_both_win(world):
    alert_id = await fire(world)
    world.clock.advance(world.settings.effective_claim_timeout_sec + 1)
    await asyncio.gather(world.say(world.priya, button=f"claim:{alert_id}"),
                         world.alerts.escalate(world.clock.now()))
    row = alert_row(world, alert_id)
    assert row["status"] == "claimed"
    assert "escalation" not in kinds(world, world.rahul)


async def test_deadlines_survive_a_restart(world):
    alert_id = await fire(world)
    fresh = Alerts(storage=world.storage, settings=world.settings, clock=world.clock,
                   adapters=world.router.adapters, send=world.router.send, household_id=world.router.household_id)
    world.clock.advance(world.settings.effective_claim_timeout_sec + 1)
    await fresh.escalate(world.clock.now())
    assert kinds(world, world.priya) == ["alert", "resend"]
    assert alert_row(world, alert_id)["escalation_level"] == 1


@pytest.mark.parametrize("message", ["got it", "I've got it", "ok I'm on it", "मैं देख लूँगा", "main dekh lunga",
                                     "ನಾನು ನೋಡ್ತೀನಿ"])
def test_claim_words(message):
    assert is_claim(message)


@pytest.mark.parametrize("message", ["Tell me if Papa gets up and I haven't got it back in 10 minutes",
                                     "yes", "check", "देखो"])
def test_not_claim_words(message):
    assert not is_claim(message)


async def test_typed_got_it_claims_the_latest_alert(world):
    alert_id = await fire(world)
    await world.say(world.priya, "got it")
    assert alert_row(world, alert_id)["status"] == "claimed"
    assert world.sent(world.priya)[-1].text == "Thanks, I've told the others."


async def test_typed_got_it_without_an_alert_is_not_a_claim(world):
    await world.say(world.rahul, "got it")  # no alert was ever sent to Rahul
    assert world.storage.query_one("SELECT COUNT(*) AS n FROM alerts")["n"] == 0
    assert world.sent(world.rahul)  # he still gets a reply (the parse path), never silence
