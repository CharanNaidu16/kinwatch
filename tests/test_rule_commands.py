"""rules / pause N / resume N / delete N in chat. Mocks only."""
import pytest

from backend.engine import Phase
from backend.router import rule_command
from tests.test_watcher import EMPTY, IN_BED, World, arm


@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


def status(world: World, rule_id: int) -> str:
    return world.storage.query_one("SELECT status FROM rules WHERE id = ?", (rule_id,))["status"]


def last(world: World, member) -> str:
    return world.sent(member)[-1].text


@pytest.mark.parametrize("message, expected", [
    ("rules", ("list", None)), ("नियम", ("list", None)), ("pause 3", ("pause", 3)), ("3 pause", ("pause", 3)),
    ("Resume 3", ("resume", 3)), ("rule 3 pause", ("pause", 3)), ("नियम 3 बंद", ("pause", 3)),
    ("delete 12", ("delete", 12)), ("ನಿಯಮ 2 ಚಾಲೂ", ("resume", 2)),
])
def test_commands_are_recognised(message, expected):
    assert rule_command(message) == expected


@pytest.mark.parametrize("message", ["pause", "Tell me if Papa leaves the bed for 3 minutes", "3", "stop it now 3",
                                     "rules for the stove please"])
def test_other_messages_are_not_commands(message):
    assert rule_command(message) is None


async def test_list_pause_resume_delete(world):
    await world.say(world.priya, "rules")
    assert last(world, world.priya).startswith("There are no rules yet")

    rule_id = await arm(world)
    await world.say(world.priya, "rules")
    listing = last(world, world.priya)
    assert listing.startswith("Your rules:") and f"{rule_id} · ▶️ on · If Papa leaves the bed" in listing
    assert f"pause {rule_id}" in listing

    await world.say(world.priya, f"pause {rule_id}")
    assert status(world, rule_id) == "paused" and rule_id not in world.watcher.runtimes
    assert last(world, world.priya).startswith(f"⏸ Rule {rule_id} is paused")
    await world.say(world.priya, f"pause {rule_id}")
    assert last(world, world.priya) == f"Rule {rule_id} is already paused."
    await world.say(world.priya, "rules")
    assert "⏸ paused" in last(world, world.priya)

    await world.say(world.rahul, f"resume {rule_id}")  # any household member may do it
    assert status(world, rule_id) == "armed" and rule_id in world.watcher.runtimes
    assert last(world, world.rahul).startswith(f"▶️ Rule {rule_id} is on again: If Papa leaves the bed")

    await world.say(world.priya, f"delete {rule_id}")
    assert status(world, rule_id) == "deleted" and rule_id not in world.watcher.runtimes
    await world.say(world.priya, f"resume {rule_id}")
    assert last(world, world.priya) == f"I don't have a rule {rule_id}. Send \"rules\" to see them."
    kinds = [r["kind"] for r in world.storage.timeline(world.router.household_id, 50)]
    assert {"rule_paused", "rule_resumed", "rule_deleted"} <= set(kinds)


async def test_resume_starts_the_timer_afresh(world):
    rule_id = await arm(world)
    for objects in [IN_BED] * 4 + [EMPTY] * 4:
        await world.see(objects)
    assert world.watcher.runtimes[rule_id].engine.phase == Phase.PENDING
    await world.say(world.priya, f"pause {rule_id}")
    world.clock.advance(3600)  # an hour later the old "since" would fire at once
    await world.say(world.priya, f"resume {rule_id}")
    engine = world.watcher.runtimes[rule_id].engine
    assert engine.phase != Phase.PENDING and engine.since is None
    await world.see(EMPTY)
    assert world.storage.query_one("SELECT COUNT(*) AS n FROM alerts")["n"] == 0


async def test_resume_without_a_stored_zone_is_refused(world):
    rule_id = await arm(world)
    await world.say(world.priya, f"pause {rule_id}")
    for f in (world.settings.data_path / "rules" / str(rule_id)).glob("*"):
        f.unlink()
    await world.say(world.priya, f"resume {rule_id}")
    assert status(world, rule_id) == "paused"
    assert last(world, world.priya).startswith(f"I no longer have the outlined photo for rule {rule_id}")


async def test_hindi_command_gets_a_hindi_reply(world):
    rule_id = await arm(world)
    await world.say(world.priya, f"नियम {rule_id} बंद")
    assert status(world, rule_id) == "paused"
    assert last(world, world.priya).startswith(f"⏸ नियम {rule_id} रोक दिया गया है")
