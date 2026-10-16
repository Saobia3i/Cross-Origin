"""Report endpoints over HTTP (fixture-seeded database; tokens minted directly, no login endpoint needed)."""
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.models import Report, utcnow
from app.security import create_access_token
from app.services import alert_service as svc
from app.tests.helpers import (
    ADMIN, MRT_M_F, USER1, USER2, USER3, headers_for, jpeg_bytes, make_user, png_bytes, report_payload,
    token_for, user_by_email,
)


@pytest.fixture
def auth(engine):
    """auth("user2@...") -> Authorization headers."""
    def make(email):
        with Session(engine) as s:
            return headers_for(s, email)
    return make


def envelope(r, ok=True):
    body = r.json()
    assert set(body) == {"ok", "data", "meta", "errors"} and body["ok"] is ok, body
    assert (body["errors"] == []) is ok
    return body


def err(r, status, code):
    assert r.status_code == status, r.text
    body = envelope(r, ok=False)
    assert body["data"] is None and body["errors"][0]["code"] == code
    return body["errors"][0]


def create(client, auth, who=USER2, **kw):
    r = client.post("/api/reports", json=report_payload(**kw), headers=auth(who))
    assert r.status_code == 201, r.text
    return r.json()["data"]["report"]


def verify(client, auth, rid, **body):
    r = client.post(f"/api/admin/reports/{rid}/verify", json=body, headers=auth(ADMIN))
    assert r.status_code == 200, r.text
    return r.json()["data"]["report"]


def closed_by(client, route_id=MRT_M_F):
    routes = {x["route_id"]: x for x in client.get("/api/transit/routes").json()["data"]}
    return routes[route_id]["closed_by"]


# ---- authentication ---------------------------------------------------------------------------------
def test_guest_cannot_report_but_can_browse(client):
    err(client.post("/api/reports", json=report_payload()), 401, "UNAUTHORIZED")
    assert client.get("/api/reports").status_code == 200
    assert client.get("/api/reports/rpt_TEST_FIXTURE_001").status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/reports/rpt_TEST_FIXTURE_001/vote", {"value": 1}),
    ("delete", "/api/reports/rpt_TEST_FIXTURE_001/vote", None),
    ("post", "/api/reports/rpt_TEST_FIXTURE_001/confirm", None),
    ("post", "/api/reports/rpt_TEST_FIXTURE_001/resolve", None),
    ("post", "/api/reports/drafts/drf_x/submit", None),
    ("post", "/api/reports/drafts/drf_x/cancel", None),
    ("post", "/api/admin/reports/rpt_TEST_FIXTURE_001/verify", None),
    ("post", "/api/admin/reports/rpt_TEST_FIXTURE_001/reject", {"reason": "x"}),
    ("post", "/api/admin/reports/rpt_TEST_FIXTURE_001/reopen", None),
])
def test_guests_cannot_vote_confirm_resolve_or_moderate(client, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    err(r, 401, "UNAUTHORIZED")


def test_bad_tokens(client, engine):
    err(client.post("/api/reports", json=report_payload(), headers={"Authorization": "Bearer junk"}), 401, "INVALID_TOKEN")
    err(client.post("/api/reports", json=report_payload(), headers={"Authorization": "Token abc"}), 401, "INVALID_TOKEN")
    expired = create_access_token("2", expires_minutes=-5)
    err(client.post("/api/reports", json=report_payload(), headers={"Authorization": f"Bearer {expired}"}), 401, "TOKEN_EXPIRED")
    ghost = create_access_token("9999")
    err(client.post("/api/reports", json=report_payload(), headers={"Authorization": f"Bearer {ghost}"}), 401, "INVALID_TOKEN")


def test_unverified_and_disabled_users_are_refused(client, engine):
    with Session(engine) as s:
        nv = {"Authorization": f"Bearer {token_for(make_user(s, 'nv@x.local', verified=False))}"}
        off = {"Authorization": f"Bearer {token_for(make_user(s, 'off@x.local', status='disabled'))}"}
    err(client.post("/api/reports", json=report_payload(), headers=nv), 403, "NOT_VERIFIED")
    err(client.post("/api/reports/rpt_TEST_FIXTURE_001/vote", json={"value": 1}, headers=nv), 403, "NOT_VERIFIED")
    err(client.post("/api/reports", json=report_payload(), headers=off), 403, "ACCOUNT_DISABLED")
    assert client.get("/api/reports", headers=nv).status_code == 200  # unverified users may still browse


def test_non_admin_cannot_moderate(client, auth):
    rid = create(client, auth)["report_id"]
    h = auth(USER1)
    err(client.post(f"/api/admin/reports/{rid}/verify", headers=h), 403, "FORBIDDEN")
    err(client.post(f"/api/admin/reports/{rid}/reject", json={"reason": "x"}, headers=h), 403, "FORBIDDEN")
    err(client.post(f"/api/admin/reports/{rid}/reopen", headers=h), 403, "FORBIDDEN")


# ---- create -------------------------------------------------------------------------------------------------
def test_verified_user_can_report(client, auth):
    r = client.post("/api/reports", json=report_payload(), headers=auth(USER2))
    body = envelope(r)
    assert r.status_code == 201
    rep = body["data"]["report"]
    assert rep["status"] == "pending" and rep["affects_routing"] is False and rep["admin_approved"] is False
    assert rep["report_id"].startswith("rpt_") and rep["half_life_min"] == 60
    assert rep["confidence"] == pytest.approx(0.45) and rep["freshness"] == pytest.approx(1.0)
    assert rep["my_vote"] is None and rep["images"] == [] and rep["flags"] == []
    assert body["data"]["sanity"] == {"flags": [], "notes": [], "needs_admin_review": False}
    assert set(rep) >= {"upvotes", "downvotes", "confidence", "freshness", "expires_at", "last_verified_at", "status",
                        "affects_routing", "needs_admin_review", "contradiction_flag", "my_vote", "images"}


def test_validation_errors(client, auth):
    h = auth(USER2)
    for body in ({}, report_payload(report_type="meteor"), report_payload(severity="apocalyptic"),
                 report_payload(description=""), report_payload(fare_amount_bdt=-1), report_payload(mode="hovercraft")):
        r = client.post("/api/reports", json=body, headers=h)
        assert r.status_code == 422 and r.json()["errors"][0]["code"] == "VALIDATION_ERROR", body
    err(client.post("/api/reports", data="not json", headers={**h, "content-type": "application/json"}), 422, "VALIDATION_ERROR")
    err(client.post("/api/reports", json=[1], headers=h), 422, "VALIDATION_ERROR")


def test_sanity_errors_come_back_as_envelopes(client, auth):
    h = auth(USER3)
    err(client.post("/api/reports", json=report_payload(affected_hub="Narnia"), headers=h), 404, "UNKNOWN_HUB")
    err(client.post("/api/reports", json=report_payload(affected_route_ids=["nope"]), headers=h), 404, "ROUTE_NOT_FOUND")
    future = (utcnow() + timedelta(hours=1)).isoformat()
    err(client.post("/api/reports", json=report_payload(incident_time=future), headers=h), 422, "INCIDENT_TIME_FUTURE")
    old = (utcnow() - timedelta(hours=30)).isoformat() + "Z"
    err(client.post("/api/reports", json=report_payload(incident_time=old), headers=h), 422, "INCIDENT_TIME_TOO_OLD")
    err(client.post("/api/reports", json=report_payload(report_type="fare_surge"), headers=h), 422, "FARE_SURGE_FIELDS_REQUIRED")


def test_timezone_aware_incident_time_is_accepted(client, auth):
    ts = (utcnow() - timedelta(minutes=30)).isoformat() + "+00:00"
    assert client.post("/api/reports", json=report_payload(incident_time=ts), headers=auth(USER3)).status_code == 201


def test_duplicate_and_unrealistic_fare_and_contradiction_flags(client, auth):
    first = create(client, auth, USER3)
    dup = client.post("/api/reports", json=report_payload(), headers=auth(USER3)).json()["data"]
    assert dup["report"]["needs_admin_review"] is True and dup["report"]["flags"] == ["duplicate"]
    assert dup["sanity"]["flags"] == ["duplicate"] and first["report_id"] in dup["sanity"]["notes"][0]
    surge = create(client, auth, USER1, report_type="fare_surge", severity="light", affected_hub=None,
                   affected_route_ids=["LOCAL_BUS_FARMGATE_UTTARA_NORTH"], mode="rickshaw", fare_amount_bdt=5000)
    assert surge["needs_admin_review"] is True and surge["flags"] == ["unrealistic_fare"] and surge["status"] == "pending"
    clash = create(client, auth, USER1, affected_hub="FARMGATE", affected_route_ids=[])
    assert clash["contradiction_flag"] is True and clash["needs_admin_review"] is True and clash["status"] == "pending"


# ---- listing & reading ---------------------------------------------------------------------------------------------
def test_list_reports_and_filters(client, auth):
    mine = create(client, auth, USER3, affected_hub="UTTARA_NORTH", affected_route_ids=[])
    r = client.get("/api/reports")
    body = envelope(r)
    ids = [x["report_id"] for x in body["data"]]
    assert ids[0] == mine["report_id"] and set(ids) == {mine["report_id"], "rpt_TEST_FIXTURE_001", "rpt_TEST_FIXTURE_002"}
    assert [x["report_id"] for x in client.get("/api/reports", params={"status": "verified"}).json()["data"]] == ["rpt_TEST_FIXTURE_002"]
    assert [x["report_id"] for x in client.get("/api/reports", params={"report_type": "all_clear"}).json()["data"]] == ["rpt_TEST_FIXTURE_002"]
    assert [x["report_id"] for x in client.get("/api/reports", params={"affected_hub": "FARMGATE"}).json()["data"]] == ["rpt_TEST_FIXTURE_002"]
    assert [x["report_id"] for x in client.get("/api/reports", params={"affected_hub": "Mirpur"}).json()["data"]] == ["rpt_TEST_FIXTURE_001"]
    assert len(client.get("/api/reports", params={"limit": 1}).json()["data"]) == 1
    future = (utcnow() + timedelta(minutes=5)).isoformat()
    assert client.get("/api/reports", params={"since": future}).json()["data"] == []
    past = (utcnow() - timedelta(hours=1)).isoformat() + "Z"
    assert len(client.get("/api/reports", params={"since": past}).json()["data"]) == 3
    for bad in ({"status": "approved"}, {"report_type": "x"}, {"limit": 0}, {"limit": 500}, {"since": "yesterday"}):
        assert client.get("/api/reports", params=bad).status_code == 422


def test_get_one_report_and_my_vote(client, auth):
    rid = "rpt_TEST_FIXTURE_001"
    guest = client.get(f"/api/reports/{rid}")
    envelope(guest)
    assert guest.json()["data"]["my_vote"] is None
    client.post(f"/api/reports/{rid}/vote", json={"value": -1}, headers=auth(USER3))
    assert client.get(f"/api/reports/{rid}", headers=auth(USER3)).json()["data"]["my_vote"] == -1
    assert client.get(f"/api/reports/{rid}", headers=auth(USER2)).json()["data"]["my_vote"] is None
    assert client.get(f"/api/reports/{rid}").json()["data"]["downvotes"] == 1
    err(client.get("/api/reports/rpt_missing"), 404, "REPORT_NOT_FOUND")
    assert [x["my_vote"] for x in client.get("/api/reports", headers=auth(USER3)).json()["data"] if x["report_id"] == rid] == [-1]


def test_reads_apply_decay_and_expiry(client, engine):
    with Session(engine) as s:
        r = s.get(Report, "rpt_TEST_FIXTURE_001")
        r.created_at = utcnow() - timedelta(hours=1)  # one half-life (60 min)
        r.expires_at = utcnow() + timedelta(hours=1)
        s.add(r)
        s.commit()
    data = client.get("/api/reports/rpt_TEST_FIXTURE_001").json()["data"]
    assert data["freshness"] == pytest.approx(0.5, abs=0.02) and data["confidence"] == pytest.approx(0.225, abs=0.02)
    with Session(engine) as s:
        r = s.get(Report, "rpt_TEST_FIXTURE_001")
        r.expires_at = utcnow() - timedelta(seconds=1)
        s.add(r)
        s.commit()
    assert client.get("/api/reports/rpt_TEST_FIXTURE_001").json()["data"]["status"] == "expired"
    assert client.get("/api/reports", params={"status": "expired"}).json()["data"][0]["freshness"] == 0


# ---- votes -----------------------------------------------------------------------------------------------------------
def test_vote_flow(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    r = client.post(f"/api/reports/{rid}/vote", json={"value": 1}, headers=auth(USER3))
    rep = envelope(r)["data"]
    assert rep["upvotes"] == 1 and rep["my_vote"] == 1 and rep["last_verified_at"] is not None
    assert rep["confidence"] == pytest.approx(0.50, abs=0.01) and rep["status"] == "pending"
    rep = client.post(f"/api/reports/{rid}/vote", json={"value": -1}, headers=auth(USER3)).json()["data"]
    assert (rep["upvotes"], rep["downvotes"], rep["my_vote"]) == (0, 1, -1)
    rep = client.delete(f"/api/reports/{rid}/vote", headers=auth(USER3)).json()["data"]
    assert (rep["upvotes"], rep["downvotes"], rep["my_vote"]) == (0, 0, None)
    err(client.delete(f"/api/reports/{rid}/vote", headers=auth(USER3)), 404, "NO_VOTE_TO_RETRACT")


def test_vote_validation_and_ownership(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    err(client.post(f"/api/reports/{rid}/vote", json={"value": 1}, headers=auth(USER2)), 403, "CANNOT_VOTE_OWN_REPORT")
    err(client.post(f"/api/reports/{rid}/confirm", headers=auth(USER2)), 403, "CANNOT_VOTE_OWN_REPORT")
    for body in ({"value": 0}, {"value": 2}, {}, {"value": "up"}):
        assert client.post(f"/api/reports/{rid}/vote", json=body, headers=auth(USER3)).status_code == 422
    err(client.post("/api/reports/rpt_missing/vote", json={"value": 1}, headers=auth(USER3)), 404, "REPORT_NOT_FOUND")


def test_confirm(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    rep = envelope(client.post(f"/api/reports/{rid}/confirm", headers=auth(USER3)))["data"]
    assert rep["upvotes"] == 1 and rep["my_vote"] == 1 and rep["status"] == "pending"


def test_votes_cannot_verify_a_report(client, auth, engine):
    rid = create(client, auth, USER2)["report_id"]
    with Session(engine) as s:
        voters = [{"Authorization": f"Bearer {token_for(make_user(s, f'v{n}@x.local'))}"} for n in range(6)]
    for h in voters:
        client.post(f"/api/reports/{rid}/vote", json={"value": 1}, headers=h)
    rep = client.get(f"/api/reports/{rid}").json()["data"]
    assert rep["status"] == "pending" and rep["upvotes"] == 6 and rep["affects_routing"] is False
    assert rep["needs_admin_review"] is True  # strong community support asks for an admin look
    assert closed_by(client) is None


# ---- moderation & routing ------------------------------------------------------------------------------------------------
def test_pending_report_does_not_close_routes_but_verified_does(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    assert closed_by(client) is None
    assert client.post("/api/trips/search", json={"origin": "Mirpur 10", "destination": "Farmgate"}).json()["data"]["routes"]
    rep = verify(client, auth, rid, edge_closed=True, note="seen it")
    assert rep["status"] == "verified" and rep["affects_routing"] is True and rep["admin_approved"] is True
    assert rep["confidence"] == pytest.approx(0.65)
    assert closed_by(client) == "verified_alert"
    data = client.post("/api/trips/search", json={"origin": "Mirpur 10", "destination": "Farmgate"}).json()["data"]
    assert data["routes"] == [] and data["no_route_reason"]["code"] == "NO_ROUTE_FOUND"
    hubs = {h["hub_id"]: h for h in client.get("/api/transit/hubs").json()["data"]}
    assert hubs["MIRPUR_10"]["status"] == "delayed" and hubs["MIRPUR_10"]["active_alert_count"] == 1


def test_reject_resolve_and_reopen_restore_or_change_routing(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    verify(client, auth, rid, edge_closed=True)
    assert closed_by(client) == "verified_alert"
    rep = client.post(f"/api/reports/{rid}/resolve", headers=auth(USER3)).json()["data"]  # any verified user
    assert rep["status"] == "resolved" and rep["affects_routing"] is False and rep["confidence"] == 0
    assert closed_by(client) is None
    err(client.post(f"/api/reports/{rid}/resolve", headers=auth(USER3)), 409, "INVALID_STATUS_TRANSITION")
    rep = client.post(f"/api/admin/reports/{rid}/reopen", json={"reason": "still flooded"}, headers=auth(ADMIN)).json()["data"]
    assert rep["status"] == "pending" and closed_by(client) is None
    verify(client, auth, rid, edge_closed=True)
    assert closed_by(client) == "verified_alert"
    rep = client.post(f"/api/admin/reports/{rid}/reject", json={"reason": "wrong junction"}, headers=auth(ADMIN)).json()["data"]
    assert rep["status"] == "rejected" and rep["affects_routing"] is False
    assert closed_by(client) is None
    err(client.post(f"/api/reports/{rid}/vote", json={"value": 1}, headers=auth(USER3)), 409, "REPORT_NOT_VOTABLE")


def test_admin_endpoints_validate_input(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    h = auth(ADMIN)
    assert client.post(f"/api/admin/reports/{rid}/reject", json={}, headers=h).status_code == 422
    assert client.post(f"/api/admin/reports/{rid}/reject", json={"reason": ""}, headers=h).status_code == 422
    err(client.post(f"/api/admin/reports/{rid}/reopen", headers=h), 409, "INVALID_STATUS_TRANSITION")
    err(client.post("/api/admin/reports/rpt_missing/verify", headers=h), 404, "REPORT_NOT_FOUND")
    assert client.post(f"/api/admin/reports/{rid}/verify", json={"extra_time_min": -1}, headers=h).status_code == 422
    assert client.post(f"/api/admin/reports/{rid}/verify", json={}, headers=h).status_code == 200  # no body needed
    err(client.post(f"/api/admin/reports/{rid}/verify", headers=h), 409, "INVALID_STATUS_TRANSITION")  # already verified


def test_admin_verify_returns_sanity_summary(client, auth):
    clash = create(client, auth, USER1, affected_hub="FARMGATE", affected_route_ids=[])
    r = client.post(f"/api/admin/reports/{clash['report_id']}/verify", headers=auth(ADMIN))
    data = envelope(r)["data"]
    assert data["report"]["status"] == "verified" and data["report"]["needs_admin_review"] is False
    assert data["sanity"]["flags"] == ["contradiction"]


def test_verified_delay_shows_on_the_route(client, auth):
    rid = create(client, auth, USER2)["report_id"]
    verify(client, auth, rid, extra_time_min=15)
    route = next(x for x in client.get("/api/transit/routes").json()["data"] if x["route_id"] == MRT_M_F)
    assert route["status"] == "active" and route["extra_time_min"] == 15 and route["active_alert_ids"] == [rid]


# ---- drafts ---------------------------------------------------------------------------------------------------------------------
def make_draft(engine, who=USER2, session_id="CHAT1", **kw):
    with Session(engine) as s:
        return svc.create_draft(s, session_id, user_by_email(s, who).id, report_payload(**kw)).draft_id


def test_submit_and_cancel_draft(client, auth, engine):
    draft_id = make_draft(engine)
    assert client.get("/api/reports").json()["data"][0]["report_id"].startswith("rpt_TEST")  # no new report yet
    r = client.post(f"/api/reports/drafts/{draft_id}/submit", headers=auth(USER2))
    data = envelope(r)["data"]
    assert r.status_code == 201 and data["report"]["status"] == "pending" and data["draft"]["status"] == "submitted"
    assert data["draft"]["draft_id"] == draft_id and data["draft"]["payload"]["report_type"] == "waterlogging"
    err(client.post(f"/api/reports/drafts/{draft_id}/submit", headers=auth(USER2)), 409, "DRAFT_NOT_OPEN")
    other = make_draft(engine, affected_hub="UTTARA_NORTH", affected_route_ids=[])
    c = client.post(f"/api/reports/drafts/{other}/cancel", headers=auth(USER2))
    assert envelope(c)["data"]["draft"]["status"] == "cancelled"
    err(client.post(f"/api/reports/drafts/{other}/submit", headers=auth(USER2)), 409, "DRAFT_NOT_OPEN")
    err(client.post("/api/reports/drafts/drf_nope/submit", headers=auth(USER2)), 404, "DRAFT_NOT_FOUND")
    err(client.post(f"/api/reports/drafts/{make_draft(engine, USER2, 'C2')}/submit", headers=auth(USER3)), 403, "DRAFT_FORBIDDEN")


def test_draft_with_bad_payload_stays_a_draft(client, auth, engine):
    draft_id = make_draft(engine, affected_hub="NOWHERE")
    err(client.post(f"/api/reports/drafts/{draft_id}/submit", headers=auth(USER2)), 404, "UNKNOWN_HUB")
    assert client.post(f"/api/reports/drafts/{draft_id}/cancel", headers=auth(USER2)).status_code == 200


# ---- images ------------------------------------------------------------------------------------------------------------------------
def multipart(client, who_headers, files=(), **fields):
    data = {"report_type": "waterlogging", "severity": "heavy", "affected_hub": "MIRPUR_10",
            "description": "Water on the road", **fields}
    return client.post("/api/reports", data=data, files=[("images", f) for f in files], headers=who_headers)


def test_multipart_report_with_images(client, auth, uploads):
    r = multipart(client, auth(USER2), [("a.png", png_bytes(), "image/png"), ("b.jpg", jpeg_bytes(), "image/jpeg")],
                  affected_route_ids=MRT_M_F)
    rep = envelope(r)["data"]["report"]
    assert r.status_code == 201 and rep["affected_route_ids"] == [MRT_M_F]
    assert [i["mime"] for i in rep["images"]] == ["image/png", "image/jpeg"]
    assert all(i["url"].startswith(f"/uploads/reports/{rep['report_id']}/") for i in rep["images"])
    stored = list((uploads / "reports" / rep["report_id"]).iterdir())
    assert len(stored) == 2 and rep["images"][0]["size_bytes"] == len(png_bytes())
    again = client.get(f"/api/reports/{rep['report_id']}").json()["data"]
    assert len(again["images"]) == 2


def test_multipart_payload_json_and_no_images(client, auth, uploads):
    import json
    r = client.post("/api/reports", data={"payload": json.dumps(report_payload())}, files=[("images", ("", b"", "application/octet-stream"))],
                    headers=auth(USER2))
    assert r.status_code == 201 and r.json()["data"]["report"]["images"] == []
    assert not uploads.exists() or not any(uploads.rglob("*.*"))
    err(multipart(client, auth(USER1), report_type="nonsense"), 422, "VALIDATION_ERROR")
    err(client.post("/api/reports", data={"payload": "{oops"}, files=[("images", ("", b"", "text/plain"))], headers=auth(USER1)),
        422, "VALIDATION_ERROR")


def test_image_rules(client, auth, uploads):
    h = auth(USER2)
    err(multipart(client, h, [("a.gif", b"GIF89a....", "image/gif")]), 422, "IMAGE_TYPE_NOT_ALLOWED")
    err(multipart(client, h, [("a.png", b"this is text", "image/png")]), 422, "IMAGE_INVALID")
    err(multipart(client, h, [(f"{n}.png", png_bytes(), "image/png") for n in range(6)]), 422, "IMAGE_TOO_MANY")
    big = png_bytes(5 * 1024 * 1024 + 10)
    err(multipart(client, h, [("big.png", big, "image/png")]), 422, "IMAGE_TOO_LARGE")
    assert multipart(client, h, [(f"{n}.png", png_bytes(), "image/png") for n in range(5)]).status_code == 201  # exactly 5 is fine
    assert len(client.get("/api/reports").json()["data"]) == 3  # the 4 refused uploads created nothing (2 fixtures + 1)


def test_text_only_json_report_still_works_with_uploads_configured(client, auth, uploads):
    assert client.post("/api/reports", json=report_payload(), headers=auth(USER2)).status_code == 201
