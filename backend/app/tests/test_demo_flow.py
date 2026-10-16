"""End-to-end demo story over real HTTP endpoints (TestClient), on the fixture-seeded database. No live LLM:
provider keys are blanked, so chat uses the rule-based parser."""
import pytest
from sqlalchemy import text as sql
from sqlmodel import Session

from app.config import settings
from app.scripts.demo_logs import Api, Demo


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for name in ("openrouter_api_key", "gemini_api_key", "groq_api_key"):
        monkeypatch.setattr(settings, name, "", raising=False)


def make_demo(client, session="DEMO_SESSION", secret=None):
    lines: list[str] = []
    demo = Demo(Api(client), session, 0, secret or settings.demo_secret, "password123", out=lines.append,
                sleep=lambda s: None)
    return demo, lines


def test_full_demo_script_runs_against_fixture_data(client):
    demo, lines = make_demo(client)
    code = demo.run()
    out = "\n".join(lines)
    assert code == 0, out
    assert set(demo.results) == set("ABCDEFGHIJKL")
    assert all(v == "ok" for v in demo.results.values()), out
    # the story's key moments really happened
    assert "status=pending" in out and "status=verified" in out and "affects_routing=True" in out
    assert "suggested safe amount" in out
    assert "blocked=True" in out and "UNKNOWN_ROUTE_ID" in out
    for kind in ("rideshare.created", "report.verified", "simulation.reset"):
        assert kind in out
    assert "must be None" in out and "active_scenario=None" in out


def test_demo_reports_missing_data_without_crashing(engine):
    """No route edges seeded: the script says what to do and never fabricates anything."""
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import app
    from app.services.seeder import seed

    with Session(engine) as s:
        seed(s, "fixtures")
        s.execute(sql("DELETE FROM route_edges"))
        s.commit()

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override
    try:
        demo, lines = make_demo(TestClient(app))
        assert demo.run() == 1
    finally:
        app.dependency_overrides.clear()
    assert "Normalized/fixture transit data not available. Run seed --source fixtures for demo." in "\n".join(lines)


def test_demo_with_wrong_demo_secret_fails_only_that_step(client):
    demo, _ = make_demo(client, secret="wrong")
    demo.run()
    assert demo.results["J"] == "FAILED" and demo.results["I"] == "ok" and demo.results["L"] == "ok"


def test_shortened_story_by_hand(client):
    """The same story as plain requests, checking the contract fields."""
    def login(email):
        r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        assert r.status_code == 200
        return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}

    h1, h2, h3, hadm = (login(f"{n}@dhakapath.local") for n in ("user1", "user2", "user3", "admin"))
    S = "DEMO_SESSION"

    chat = client.post("/api/chat", json={"session_id": S, "message": "Mirpur 10 theke Farmgate jete chai, student fare dao"}).json()
    assert chat["ok"] and chat["data"]["reply_text"] and chat["data"]["grounding"]["valid"] is True

    draft = client.post("/api/chat", headers=h1, json={
        "session_id": S, "message": "Mirpur 10 e onek pani jome ache, bike chola jachhe na"}).json()["data"]
    card = draft["structured_payload"]["draft_report"]
    assert card["status"] == "draft"

    report = client.post(f"/api/reports/drafts/{card['draft_id']}/submit", headers=h1).json()["data"]["report"]
    assert report["status"] == "pending" and report["affects_routing"] is False
    rid = report["report_id"]
    for h in (h2, h3):
        assert client.post(f"/api/reports/{rid}/vote", headers=h, json={"value": 1}).json()["data"]["upvotes"] >= 1
    verified = client.post(f"/api/admin/reports/{rid}/verify", headers=hadm, json={"extra_time_min": 10}).json()["data"]["report"]
    assert verified["status"] == "verified" and verified["affects_routing"] is True

    sim = client.post("/api/simulations", headers=h1, json={"session_id": S, "scenario_type": "heavy_rain"}).json()["data"]
    assert sim["status"] == "active"
    other = client.post("/api/trips/search", json={"origin": "Mirpur 10", "destination": "Farmgate",
                                                  "session_id": "SOMEONE_ELSE"}).json()["data"]
    assert other["active_scenario"] is None  # no leakage across sessions
    assert client.post("/api/simulations/reset", headers=h1, json={"session_id": S}).json()["data"]["status"] == "reset"

    fake = client.post("/api/grounding/fake-test", headers={"X-Demo-Secret": settings.demo_secret},
                       json={"route_ids": ["BUS_MIRPUR_MOTIJHEEL_DIRECT"], "claimed_fare_bdt": 25}).json()["data"]
    assert fake["blocked"] is True

    post = client.post("/api/rideshare/posts", headers=h1, json={
        "mode": "rickshaw", "origin_hub": "Mirpur 10", "destination_hub": "Farmgate", "seats_total": 3}).json()["data"]
    for h in (h2, h3):
        post = client.post(f"/api/rideshare/posts/{post['post_id']}/join", headers=h).json()["data"]
    assert post["status"] == "full" and post["seats_taken"] == 3

    ev = client.get("/api/events", params={"since_id": 0, "session_id": S, "limit": 100}).json()["data"]
    types = [e["event_type"] for e in ev["events"]]
    for t in ("report.draft.created", "report.created", "report.verified", "simulation.applied", "simulation.reset",
              "rideshare.created", "rideshare.joined"):
        assert t in types, t
    assert ev["latest_id"] == ev["events"][-1]["event_id"]
    assert set(ev["events"][0]) == {"event_id", "event_type", "session_id", "report_id", "route_id", "payload", "created_at"}
    again = client.get("/api/events", params={"since_id": ev["latest_id"], "session_id": S}).json()["data"]
    assert again["events"] == [] and again["latest_id"] == ev["latest_id"]  # polling from the cursor: nothing new


def test_auth_endpoints(client):
    ok = client.post("/api/auth/login", json={"email": "USER1@dhakapath.local", "password": "password123"})
    assert ok.status_code == 200 and ok.json()["data"]["user"]["email"] == "user1@dhakapath.local"
    assert "password_hash" not in ok.text
    bad = client.post("/api/auth/login", json={"email": "user1@dhakapath.local", "password": "nope"})
    assert bad.status_code == 401 and bad.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"
    reg = client.post("/api/auth/register", json={"email": "new@example.com", "name": "New", "password": "longenough1"})
    assert reg.status_code == 201 and reg.json()["data"]["user"]["is_verified"] is False
    assert client.post("/api/auth/register", json={"email": "new@example.com", "name": "N", "password": "longenough1"}).status_code == 409
    token = reg.json()["data"]["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["data"]["role"] == "user"
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/rideshare/posts", headers={"Authorization": f"Bearer {token}"},
                    json={"mode": "cng", "origin_hub": "Farmgate", "seats_total": 2})
    assert r.status_code == 403  # a fresh account is not verified


def test_every_catalog_endpoint_is_implemented_or_clear_501(client):
    for e in client.get("/api/catalog").json()["data"]["endpoints"]:
        if e["implemented"]:
            continue
        path = e["path"].replace("{route_id}", "x").replace("{hub_id}", "x").replace("{mode}", "bus")
        r = client.request(e["method"], path, json={})
        assert r.status_code == 501 and r.json()["errors"][0]["code"] == "NOT_IMPLEMENTED", (e["method"], e["path"])
