from fastapi.testclient import TestClient

from backend.config import FamilyMember
from backend.main import create_app


def test_app_boots_with_mocks_and_seeds_household(settings, storage, clock):
    settings.family_members_json = [
        FamilyMember(name="Priya", address="910000000001", is_admin=True),
        FamilyMember(name="Rahul", address="910000000002"),
    ]
    app = create_app(settings, clock=clock, storage=storage)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {
            "ok": True, "mode": "live", "use_mocks": True, "demo_mode": False, "adapters": "mock"}
        hid = app.state.household_id
        assert [m.name for m in storage.members(hid)] == ["Priya", "Rahul"]
    # restarting doesn't duplicate members
    with TestClient(create_app(settings, clock=clock, storage=storage)):
        assert len(storage.members(hid)) == 2


def test_budget_endpoint_needs_the_token(settings, storage, clock):
    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        assert client.get("/api/budget").status_code == 401
        assert client.get("/api/budget", params={"t": "wrong"}).status_code == 401
        r = client.get("/api/budget", headers={"X-BTS-Token": settings.bts_token.get_secret_value()})
        assert r.status_code == 200
        assert r.json()["mode"] == "MOCK" and r.json()["total_usd"] == 0.0
