"""Admin CMS endpoints: road block/unblock, fare rules and user verification (fixture-seeded database)."""
import pytest
from sqlmodel import Session

from app.models import RouteEdge
from app.tests.helpers import ADMIN, MRT_M_F, USER1, headers_for, make_user


@pytest.fixture
def auth(engine):
    def make(email):
        with Session(engine) as s:
            return headers_for(s, email)
    return make


def search(client):
    r = client.post("/api/trips/search", json={"origin": "Mirpur 10", "destination": "Farmgate",
                                               "prefer_student_fare": False, "optimize": "fastest"})
    assert r.status_code == 200, r.text
    return {leg["route_id"] for route in r.json()["data"]["routes"] for leg in route["legs"]}


def test_admin_endpoints_need_admin(client, auth):
    assert client.post(f"/api/admin/routes/{MRT_M_F}/block").status_code == 401
    assert client.post(f"/api/admin/routes/{MRT_M_F}/block", headers=auth(USER1)).status_code == 403
    assert client.get("/api/admin/users", headers=auth(USER1)).status_code == 403


def test_block_and_unblock_route_changes_search(client, auth, engine):
    assert MRT_M_F in search(client)
    r = client.post(f"/api/admin/routes/{MRT_M_F}/block", json={"reason": "flooded"}, headers=auth(ADMIN))
    assert r.status_code == 200 and r.json()["data"]["status"] == "closed"
    with Session(engine) as s:
        assert s.get(RouteEdge, MRT_M_F).closed_reason == "flooded"
    assert MRT_M_F not in search(client)

    r = client.post(f"/api/admin/routes/{MRT_M_F}/unblock", headers=auth(ADMIN))
    assert r.status_code == 200 and r.json()["data"]["status"] == "active"
    assert MRT_M_F in search(client)
    events = client.get("/api/events", params={"since_id": 0}).json()["data"]["events"]
    assert [e["payload"]["action"] for e in events if e["event_type"] == "route.recalculated"] == ["blocked", "unblocked"]


def test_block_unknown_route_is_404(client, auth):
    assert client.post("/api/admin/routes/NOPE/block", headers=auth(ADMIN)).status_code == 404


def test_fare_rules_list_and_replace(client, auth):
    rules = client.get("/api/admin/fare-rules", headers=auth(ADMIN)).json()["data"]
    assert rules and {"mode", "student_discount_percent"} <= set(rules[0])
    body = {"student_discount_percent": 25, "surge_allowed": True}
    r = client.put("/api/admin/fare-rules/local_bus", json=body, headers=auth(ADMIN))
    assert r.status_code == 200 and r.json()["data"]["student_discount_percent"] == 25
    assert client.put("/api/admin/fare-rules/spaceship", json=body, headers=auth(ADMIN)).status_code == 422


def test_admin_verifies_registered_user(client, auth, engine):
    with Session(engine) as s:
        new = make_user(s, "fresh@example.com", verified=False)
        new_id = new.id
    assert client.get("/api/admin/users", headers=auth(ADMIN)).status_code == 200
    r = client.post(f"/api/admin/users/{new_id}/verify", headers=auth(ADMIN))
    assert r.status_code == 200 and r.json()["data"]["is_verified"] is True
    assert client.post("/api/admin/users/99999/verify", headers=auth(ADMIN)).status_code == 404
