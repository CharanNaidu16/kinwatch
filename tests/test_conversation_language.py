"""The conversation stays in the language the person writes in; each recipient reads alerts in theirs."""
import json

import pytest

from backend.router import shown_language
from tests.test_escalation import fire, kinds, timeout
from tests.test_watcher import EMPTY, IN_BED, WALKING, World, arm


@pytest.fixture
def world(settings, storage, clock):
    return World(settings, storage, clock)


@pytest.mark.parametrize("message, lang", [
    ("ok", None), ("3", None), ("check", None), ("pause 4", None), ("rules", None),
    ("can you check the sofa", "en"), ("हाँ", "hi"), ("theek hai", "hi"), ("ಹೌದು", "kn"),
])
def test_shown_language(message, lang):
    assert shown_language(message) == lang


async def test_a_hindi_conversation_stays_hindi_on_short_replies(world):
    await world.say(world.priya, "पापा सोफ़े पर हैं?")  # Hindi
    await world.see(IN_BED)
    await world.say(world.priya, "check")  # short and Latin: still Hindi
    assert world.sent(world.priya)[-1].text == "कैमरे में अभी यह दिख रहा है।"
    await world.say(world.priya, "rules")
    assert world.sent(world.priya)[-1].text.startswith("अभी कोई नियम नहीं है")


async def test_switching_language_switches_the_conversation(world):
    await world.say(world.priya, "पापा सोफ़े पर हैं?")
    await world.say(world.priya, "can you show me the camera")  # clearly English now
    await world.see(IN_BED)
    await world.say(world.priya, "check")
    assert world.sent(world.priya)[-1].text == "This is what the camera sees right now."


async def test_each_recipient_reads_the_alert_in_their_language(world):
    await world.say(world.rahul, "मैं ठीक से देख रहा हूँ")  # Rahul writes Hindi
    world.storage.execute("UPDATE rules SET status = 'deleted'")  # that message isn't a rule test
    world.storage.execute("UPDATE members SET last_language = 'hi' WHERE id = ?", (world.rahul.id,))
    alert_id = await fire(world)  # English rule (notify: Priya); escalation brings in Rahul
    await timeout(world)
    await timeout(world)
    assert kinds(world, world.rahul) == ["escalation"]
    to_rahul = world.sent(world.rahul)[-1]
    assert to_rahul.text.startswith("⚠️ Priya ने जवाब नहीं दिया")
    assert "बिस्तर" in to_rahul.text  # the alert itself in Hindi too
    assert to_rahul.buttons[0].title == "मैं देख लूँगा"
    await world.say(world.rahul, button=f"claim:{alert_id}")
    assert world.sent(world.rahul)[-1].text == "धन्यवाद, मैंने बाकी लोगों को बता दिया है।"
    assert world.sent(world.priya)[-1].text.startswith("Rahul is handling it")  # Priya stays English


async def test_both_languages_are_written_when_both_are_notified(world, monkeypatch):
    written = []
    original = world.router.adapters.spark.write_alert

    async def spy(facts):
        written.append(facts.language)
        return await original(facts)
    monkeypatch.setattr(world.router.adapters.spark, "write_alert", spy)
    await arm(world)  # an English rule
    await world.say(world.priya, "theek hai, dekhte hain")  # then Priya carries on in Hindi
    for objects in [IN_BED] * 4 + [WALKING] + [EMPTY] * 20:
        await world.see(objects)
    row = world.storage.query_one("SELECT language, texts_json FROM alerts")
    assert row["language"] == "en" and set(json.loads(row["texts_json"])) == {"en", "hi"}
    assert sorted(written) == ["en", "hi"]
    alert = next(s for s in world.sent(world.priya) if s.buttons and s.buttons[0].id.startswith("claim:"))
    assert "बिस्तर" in alert.text
