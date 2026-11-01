"""Recording takes, keeping the good ones without real names, and serving them for replay."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.config import Settings
from backend.main import create_app
from backend.router import describe_inbound, record_command
from backend.messaging.base import Inbound
from backend.sessions import SessionRecorder, keep, list_sessions, read_jsonl, sample_names
from tests import wa
from tests.test_router import ONE_BED, PRIYA, RAHUL, app, camera, client, outbox, post  # noqa: F401 (fixtures)


def token(client) -> str:
    return client.app.state.settings.bts_token.get_secret_value()


@pytest.mark.parametrize("message, expected", [
    ("record start", ("start", None)), ("Record stop", ("stop", None)), ("record list", ("list", None)),
    ("record delete take-2", ("delete", "take-2")), ("record", None), ("record the sofa tonight", None),
])
def test_record_commands(message, expected):
    assert record_command(message) == expected


def test_inbound_descriptions():
    def inbound(kind, **kw):
        return Inbound("m", "mock", "1", kind, 0.0, **kw)
    assert describe_inbound(inbound("button", button_payload="claim:3")) == "[taps I've got it]"
    assert describe_inbound(inbound("button", button_payload="yes:4")) == "[taps Yes]"
    assert describe_inbound(inbound("voice", media_ref="x")) == "🎤 voice note"
    assert describe_inbound(inbound("text", text="hello")) == "hello"


def test_recorder_writes_states_frames_and_chat(tmp_path, clock):
    rec = SessionRecorder(tmp_path, clock, spent=lambda: 1.5)
    rec.state({"now": "x"})  # nothing recorded before start
    assert rec.start() == "take-1"
    rec.state({"now": "a", "budget": {"total_usd": 1.75}, "rules": []})
    rec.state({"now": "b", "budget": {"total_usd": 1.75}, "rules": []})  # unchanged: not stored again
    rec.frame(b"\xff\xd8one")
    clock.advance(0.5)
    rec.frame(b"\xff\xd8too-soon")  # at most one frame a second
    clock.advance(1.0)
    rec.frame(b"\xff\xd8two")
    rec.chat("out", "Priya", "photo for you", b"\xff\xd8p", ["Yes ✓", "No ✗"])
    summary = rec.stop()
    assert summary["frames"] == 2 and summary["messages"] == 1 and summary["duration_sec"] == 1.5
    folder = tmp_path / "sessions" / "take-1"
    states = read_jsonl(folder / "states.jsonl")
    assert len(states) == 1 and states[0]["state"]["budget"]["session_usd"] == 0.25
    assert [f["file"] for f in read_jsonl(folder / "frames.jsonl")] == ["frames/00001.jpg", "frames/00002.jpg"]
    [msg] = read_jsonl(folder / "chat.jsonl")
    assert msg["photo"] == "photos/0001.jpg" and msg["buttons"] == ["Yes ✓", "No ✗"]
    assert rec.start() == "take-2"  # numbering continues
    rec.stop()
    assert [t["name"] for t in rec.takes()] == ["take-1", "take-2"]
    assert rec.delete("take-1") and not rec.delete("take-1") and not rec.delete("../etc")


def test_keep_trims_retimes_and_swaps_names(tmp_path, clock):
    rec = SessionRecorder(tmp_path, clock)
    rec.start()
    rec.state({"family": ["Charan", "Ronny"], "rules": [{"created_by": "Ronny"}]})
    for i in range(10):
        clock.advance(1.0)
        rec.frame(bytes([0xFF, 0xD8, i]))
        rec.chat("in" if i % 2 else "out", "Ronny", f"message {i} from Ronny to Charan")
    rec.stop()
    names = sample_names(["Charan", "Ronny"])
    assert names == {"Charan": "Priya", "Ronny": "Rahul"}
    out = keep(tmp_path / "sessions" / "take-1", tmp_path / "kept" / "sofa-demo", names, start=3.0, end=7.0,
               title="Sofa demo")
    dest = tmp_path / "kept" / "sofa-demo"
    assert out["duration_sec"] == 4.0 and out["title"] == "Sofa demo" and out["frames"] == 5
    raw = "".join(p.read_text(encoding="utf-8") for p in dest.glob("*.json*"))
    assert "Charan" not in raw and "Ronny" not in raw and "Rahul" in raw and "Priya" in raw
    chat = read_jsonl(dest / "chat.jsonl")
    assert chat[0]["t"] == 0.0 and chat[-1]["t"] == 4.0
    states = read_jsonl(dest / "states.jsonl")
    assert states[0]["t"] == 0.0 and states[0]["state"]["family"] == ["Priya", "Rahul"]  # the picture as of 3 s
    assert len(list((dest / "frames").glob("*.jpg"))) == 5  # frames outside the window stay behind


def test_sample_name_overrides():
    assert sample_names(["Charan", "Ronny"], {"Charan": "Arjun"}) == {"Charan": "Arjun", "Ronny": "Priya"}


def test_recording_through_the_chat(client):
    post(client, wa.text(RAHUL, "record start", "m0"))  # not an admin: just an ordinary message
    assert not client.app.state.sessions.active
    post(client, wa.text(PRIYA, "record start", "m1"))
    assert outbox(client)[-1].text.startswith("⏺ Recording take-1")
    camera(client)
    post(client, wa.text(PRIYA, "check", "m2"))
    post(client, wa.text(PRIYA, "record stop", "m3"))
    assert outbox(client)[-1].text.startswith("⏹ Saved take-1")
    folder = client.app.state.settings.data_path / "sessions" / "take-1"
    chat = read_jsonl(folder / "chat.jsonl")
    texts = [(c["dir"], c["text"]) for c in chat]
    assert ("in", "check") in texts and any(d == "out" and "camera sees" in t for d, t in texts)
    assert not any("record" in t.casefold() for _, t in texts)  # the take's own controls aren't in it
    assert any("photo" in c for c in chat) and read_jsonl(folder / "frames.jsonl")
    post(client, wa.text(PRIYA, "record list", "m4"))
    assert "take-1" in outbox(client)[-1].text
    post(client, wa.text(PRIYA, "record delete take-1", "m5"))
    assert outbox(client)[-1].text == "🗑 Deleted take-1." and not folder.exists()


def test_takes_are_private_in_live_mode(client):
    post(client, wa.text(PRIYA, "record start", "m1"))
    post(client, wa.text(PRIYA, "record stop", "m2"))
    assert client.get("/api/replay/sessions").status_code == 401
    assert client.get("/api/replay/files/takes/take-1/session.json").status_code == 401
    listing = client.get("/api/replay/sessions", headers={"X-BTS-Token": token(client)}).json()
    assert listing["mode"] == "live" and [t["name"] for t in listing["takes"]] == ["take-1"]
    r = client.get("/api/replay/files/takes/take-1/session.json", headers={"X-BTS-Token": token(client)})
    assert r.status_code == 200 and r.json()["name"] == "take-1"
    for bad in ("../../.env", "session.json/../x", "frames/../../x.jpg"):
        assert client.get(f"/api/replay/files/takes/take-1/{bad}",
                          headers={"X-BTS-Token": token(client)}).status_code == 404


def test_replay_mode_serves_kept_sessions_publicly(tmp_path, clock, monkeypatch):
    kept = tmp_path / "kept"
    (kept / "demo" / "frames").mkdir(parents=True)
    (kept / "demo" / "session.json").write_text(json.dumps({"name": "demo", "duration_sec": 5}), encoding="utf-8")
    (kept / "demo" / "frames" / "00001.jpg").write_bytes(b"\xff\xd8x")
    monkeypatch.setattr(sessions, "KEPT_DIR", kept)
    import backend.main as main
    monkeypatch.setattr(main, "KEPT_DIR", kept)
    settings = Settings(_env_file=None, mode="replay", use_mocks=True, data_dir=tmp_path / "data")
    with TestClient(create_app(settings, clock=clock)) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/bts"
        assert c.get("/bts").status_code == 200  # no token needed: replay only
        listing = c.get("/api/replay/sessions").json()
        assert listing["mode"] == "replay" and [s["name"] for s in listing["kept"]] == ["demo"]
        assert c.get("/api/replay/files/kept/demo/frames/00001.jpg").content == b"\xff\xd8x"
        assert c.get("/api/replay/files/takes/demo/session.json").status_code == 404  # no takes in replay
        assert c.get("/api/bts/state").status_code == 401  # nothing live is exposed
        assert c.post("/api/frame", content=b"x").status_code in (404, 405)  # no camera upload


def test_kept_folder_listing_is_empty_when_missing(tmp_path):
    assert list_sessions(tmp_path / "nothing") == []
