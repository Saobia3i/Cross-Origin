"""Ride-share board + fare-split bot: rules, fare estimates, events and the HTTP endpoints.

World: fixture network + an explicit CNG fare row FARMGATE<->UTTARA_NORTH (normal 100-160 BDT) so totals are exact.
"""
import math

import pytest
from sqlmodel import select

from app.enums import EventType
from app.models import Event, FareMatrixRow, RideshareJoin, RidesharePost, RouteEdge, utcnow
from app.responses import ApiError
from app.schemas import RideShareCreate, RideShareFilters
from app.services import rideshare_service as svc
from app.tests.helpers import ADMIN, USER1, USER2, USER3, headers_for, make_user, user_by_email


@pytest.fixture
def world(seeded):
    s = seeded
    s.add(FareMatrixRow(mode="cng", from_hub="FARMGATE", to_hub="UTTARA_NORTH", road_distance_km=12.0,
                        normal_low_bdt=100, normal_high_bdt=160))
    s.commit()
    return s


def payload(**kw) -> RideShareCreate:
    base = dict(mode="cng", origin_hub="Farmgate", destination_hub="Uttara North", seats_total=3)
    base.update(kw)
    return RideShareCreate(**base)


def code_of(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


def events(s, kind):
    return s.exec(select(Event).where(Event.event_type == kind)).all()


# ----------------------------------------------------------------------------- create
def test_guest_cannot_create(world):
    with pytest.raises(ApiError) as e:
        svc.create_post(world, None, payload())
    assert code_of(e) == "UNAUTHORIZED"
    assert world.exec(select(RidesharePost)).all() == []


def test_unverified_user_cannot_create(world):
    stranger = make_user(world, "new@dhakapath.local", verified=False)
    with pytest.raises(ApiError) as e:
        svc.create_post(world, stranger, payload())
    assert code_of(e) == "NOT_VERIFIED"


def test_verified_user_can_create_and_creator_seat_counted(world):
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(seats_total=4))
    assert post.status == "active" and post.seats_taken == 1 and post.seats_total == 4
    d = svc.post_detail(world, post, u)
    assert d["seats_remaining"] == 3 and d["my_participation"] is True
    assert [p["user_id"] for p in d["participants"]] == [str(u.id)]
    assert d["created_by"] == {"user_id": str(u.id), "name": u.name}
    assert d["origin_hub"] == "FARMGATE" and d["destination_hub"] == "UTTARA_NORTH"
    assert int((post.expires_at - post.created_at).total_seconds()) == 120 * 60  # default expiry
    assert len(events(world, "rideshare.created")) == 1


def test_create_validation(world):
    u = user_by_email(world, USER1)
    for bad, code in [(dict(mode="walk"), "RIDESHARE_MODE_NOT_SHAREABLE"), (dict(mode="mrt"), "RIDESHARE_MODE_NOT_SHAREABLE"),
                      (dict(mode="hovercraft"), "VALIDATION_ERROR"),
                      (dict(origin_hub=None, hub_id=None), "RIDESHARE_LOCATION_REQUIRED"),
                      (dict(origin_hub="Narnia"), "UNKNOWN_HUB"),
                      (dict(destination_hub="Farmgate"), "SAME_ORIGIN_DESTINATION")]:
        with pytest.raises(ApiError) as e:
            svc.create_post(world, u, payload(**bad))
        assert code_of(e) == code, bad
    with pytest.raises(Exception):
        payload(seats_total=1)  # a ride needs at least 2 seats
    assert world.exec(select(RidesharePost)).all() == []


def test_custom_expiry_and_scenario(world):
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(expires_in_minutes=30, scenario="heavy_rain"))
    assert int((post.expires_at - post.created_at).total_seconds()) == 30 * 60
    assert post.scenario == "rain"


# ----------------------------------------------------------------------------- fare estimate & split
def test_estimate_comes_from_fare_service_and_creates_no_edge(world):
    edges_before = len(world.exec(select(RouteEdge)).all())
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(seats_total=4))
    assert (post.estimated_total_fare_min_bdt, post.estimated_total_fare_max_bdt) == (100, 160)
    assert len(world.exec(select(RouteEdge)).all()) == edges_before  # no fake route edge
    assert world.get(RouteEdge, "RIDESHARE_ESTIMATE") is None
    d = svc.post_detail(world, post, u)
    assert "NO_ROUTE_EDGE" in [w["code"] for w in d["warnings"]]


def test_fare_split_is_a_range_with_ceil_upper_bound(world):
    u1, u2, u3 = (user_by_email(world, e) for e in (USER1, USER2, USER3))
    post = svc.create_post(world, u1, payload(seats_total=4))
    d = svc.post_detail(world, post, u1)
    assert (d["per_person_min_bdt"], d["per_person_max_bdt"]) == (100.0, 160.0)  # alone: whole fare
    svc.join_post(world, post.post_id, u2)
    svc.join_post(world, post.post_id, u3)
    post = world.get(RidesharePost, post.post_id)
    split = svc.recalculate_fare_split(post)
    assert split.participants == 3
    assert (split.total_fare_min_bdt, split.total_fare_max_bdt) == (100, 160)
    assert split.per_person_min_bdt == pytest.approx(33.33, abs=0.01)
    assert split.per_person_max_bdt == pytest.approx(53.33, abs=0.01)
    assert split.per_person_min_bdt < split.per_person_max_bdt  # a range, not a single number
    assert split.suggested_safe_per_person_bdt == math.ceil(160 / 3) == 54
    assert split.currency == "BDT" and "upper-bound" in split.note
    d = svc.post_detail(world, post, u1)
    assert d["suggested_safe_per_person_bdt"] == 54 and isinstance(d["suggested_safe_per_person_bdt"], int)
    assert "not an official tariff" in d["fare_disclaimer"]


def test_split_changes_as_people_join_and_leave(world):
    u1, u2 = user_by_email(world, USER1), user_by_email(world, USER2)
    post = svc.create_post(world, u1, payload(seats_total=4))
    svc.join_post(world, post.post_id, u2)
    assert svc.recalculate_fare_split(world.get(RidesharePost, post.post_id)).suggested_safe_per_person_bdt == 80
    svc.leave_post(world, post.post_id, u2)
    assert svc.recalculate_fare_split(world.get(RidesharePost, post.post_id)).suggested_safe_per_person_bdt == 160


def test_formula_fallback_when_no_fare_row(world):
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(mode="rickshaw"))  # no rickshaw fare row / edge for this pair
    d = svc.post_detail(world, post, u)
    codes = [w["code"] for w in d["warnings"]]
    if post.estimated_total_fare_min_bdt is None:
        assert "FARE_ESTIMATE_UNAVAILABLE" in codes
    else:
        assert "NO_ROUTE_EDGE" in codes and "FARE_IS_ESTIMATE" in codes
        assert d["estimated_total_fare_min_bdt"] <= d["estimated_total_fare_max_bdt"]


def test_unavailable_estimate_still_allows_post(world):
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(origin_hub="Farmgate", destination_hub=None))  # no destination
    d = svc.post_detail(world, post, u)
    assert post.status == "active"
    assert d["estimated_total_fare_min_bdt"] is None and d["per_person_max_bdt"] is None
    assert d["suggested_safe_per_person_bdt"] is None
    assert "FARE_ESTIMATE_UNAVAILABLE" in [w["code"] for w in d["warnings"]]


def test_hub_id_only_post(world):
    u = user_by_email(world, USER1)
    post = svc.create_post(world, u, payload(origin_hub=None, destination_hub=None, hub_id="MIRPUR_10"))
    assert post.hub_id == "MIRPUR_10" and post.origin_hub is None
    assert post.estimated_total_fare_min_bdt is None


def test_no_fake_official_fare(world):
    u = user_by_email(world, USER1)
    d = svc.post_detail(world, svc.create_post(world, u, payload()), u)
    text = str(d).lower()
    assert "official" not in text.replace("not an official tariff", "").replace("not official", "")
    assert d["warnings"][0]["code"] in ("FARE_IS_ESTIMATE", "NO_ROUTE_EDGE")
    assert not any(k in text for k in ("payment", "paid", "invoice", "transaction", "card_number"))


def test_no_payment_fields(world):
    u = user_by_email(world, USER1)
    d = svc.post_detail(world, svc.create_post(world, u, payload()), u)

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    assert not [k for k in keys(d) if any(w in k.lower() for w in ("pay", "price", "wallet", "bkash", "card"))]
    assert not [c for c in RidesharePost.model_fields if "pay" in c]


# ----------------------------------------------------------------------------- join / leave / cancel
def test_join_increments_and_full_status(world):
    u1, u2, u3 = (user_by_email(world, e) for e in (USER1, USER2, USER3))
    post = svc.create_post(world, u1, payload(seats_total=3))
    svc.join_post(world, post.post_id, u2)
    assert world.get(RidesharePost, post.post_id).seats_taken == 2
    assert world.get(RidesharePost, post.post_id).status == "active"
    svc.join_post(world, post.post_id, u3)
    full = world.get(RidesharePost, post.post_id)
    assert full.seats_taken == 3 == full.seats_total and full.status == "full"
    assert len(events(world, "rideshare.joined")) == 2
    extra = make_user(world, "extra@dhakapath.local")
    with pytest.raises(ApiError) as e:
        svc.join_post(world, post.post_id, extra)
    assert code_of(e) == "RIDESHARE_FULL" and e.value.status_code == 409


def test_joining_twice_or_own_post_is_a_noop(world):
    u1, u2 = user_by_email(world, USER1), user_by_email(world, USER2)
    post = svc.create_post(world, u1, payload())
    svc.join_post(world, post.post_id, u1)  # creator already holds a seat
    svc.join_post(world, post.post_id, u2)
    svc.join_post(world, post.post_id, u2)
    p = world.get(RidesharePost, post.post_id)
    assert p.seats_taken == 2
    assert len(world.exec(select(RideshareJoin).where(RideshareJoin.post_id == post.post_id)).all()) == 2
    assert len(events(world, "rideshare.joined")) == 1


def test_guest_and_unverified_cannot_join(world):
    post = svc.create_post(world, user_by_email(world, USER1), payload())
    with pytest.raises(ApiError) as e:
        svc.join_post(world, post.post_id, None)
    assert code_of(e) == "UNAUTHORIZED"
    with pytest.raises(ApiError) as e:
        svc.join_post(world, post.post_id, make_user(world, "nv@dhakapath.local", verified=False))
    assert code_of(e) == "NOT_VERIFIED"


def test_leave_decrements_and_reopens_full_post(world):
    u1, u2 = user_by_email(world, USER1), user_by_email(world, USER2)
    post = svc.create_post(world, u1, payload(seats_total=2))
    svc.join_post(world, post.post_id, u2)
    assert world.get(RidesharePost, post.post_id).status == "full"
    svc.leave_post(world, post.post_id, u2)
    p = world.get(RidesharePost, post.post_id)
    assert p.seats_taken == 1 and p.status == "active"
    assert len(events(world, "rideshare.left")) == 1
    d = svc.post_detail(world, p, u2)
    assert d["my_participation"] is False


def test_leave_requires_participation(world):
    post = svc.create_post(world, user_by_email(world, USER1), payload())
    with pytest.raises(ApiError) as e:
        svc.leave_post(world, post.post_id, user_by_email(world, USER2))
    assert code_of(e) == "NOT_A_PARTICIPANT"


def test_last_participant_leaving_cancels_post(world):
    u1 = user_by_email(world, USER1)
    post = svc.create_post(world, u1, payload())
    svc.leave_post(world, post.post_id, u1)
    p = world.get(RidesharePost, post.post_id)
    assert p.status == "cancelled" and p.seats_taken == 0
    assert len(events(world, "rideshare.left")) == 1 and len(events(world, "rideshare.cancelled")) == 1


def test_cancel_by_creator(world):
    u1, u2 = user_by_email(world, USER1), user_by_email(world, USER2)
    post = svc.create_post(world, u1, payload())
    svc.join_post(world, post.post_id, u2)
    with pytest.raises(ApiError) as e:
        svc.cancel_post(world, post.post_id, u2)  # participant, not creator
    assert code_of(e) == "FORBIDDEN"
    svc.cancel_post(world, post.post_id, u1)
    assert world.get(RidesharePost, post.post_id).status == "cancelled"
    assert len(events(world, "rideshare.cancelled")) == 1
    with pytest.raises(ApiError) as e:
        svc.join_post(world, post.post_id, user_by_email(world, USER3))
    assert code_of(e) == "RIDESHARE_NOT_OPEN"
    with pytest.raises(ApiError) as e:
        svc.cancel_post(world, post.post_id, u1)
    assert code_of(e) == "RIDESHARE_NOT_OPEN"


def test_admin_can_cancel(world):
    post = svc.create_post(world, user_by_email(world, USER1), payload())
    svc.cancel_post(world, post.post_id, user_by_email(world, ADMIN))
    assert world.get(RidesharePost, post.post_id).status == "cancelled"


def test_expired_post_cannot_be_joined(world):
    post = svc.create_post(world, user_by_email(world, USER1), payload())
    post.expires_at = utcnow().replace(microsecond=0) - __import__("datetime").timedelta(minutes=1)
    world.add(post)
    world.commit()
    with pytest.raises(ApiError) as e:
        svc.join_post(world, post.post_id, user_by_email(world, USER2))
    assert code_of(e) == "RIDESHARE_NOT_OPEN"
    assert world.get(RidesharePost, post.post_id).status == "expired"
    assert svc.list_posts(world) == []


def test_unknown_post(world):
    with pytest.raises(ApiError) as e:
        svc.get_post(world, "rs_nope", None)
    assert code_of(e) == "RIDESHARE_NOT_FOUND" and e.value.status_code == 404


# ----------------------------------------------------------------------------- list filters
def test_list_filters(world):
    u1, u2 = user_by_email(world, USER1), user_by_email(world, USER2)
    a = svc.create_post(world, u1, payload())
    b = svc.create_post(world, u2, payload(mode="rickshaw", origin_hub="Mirpur 10", destination_hub="Farmgate"))
    c = svc.create_post(world, u2, payload(mode="cng", origin_hub="Mirpur 10", destination_hub=None))
    svc.cancel_post(world, c.post_id, u2)

    def ids(**kw):
        return {p.post_id for p in svc.list_posts(world, RideShareFilters(**kw))}

    assert ids() == {a.post_id, b.post_id}  # cancelled hidden by default
    assert ids(active_only=False) == {a.post_id, b.post_id, c.post_id}
    assert ids(mode="cng") == {a.post_id}
    assert ids(origin_hub="Mirpur 10") == {b.post_id}
    assert ids(destination_hub="FARMGATE") == {b.post_id}
    assert ids(hub_id="FARMGATE") == {a.post_id, b.post_id}
    assert ids(status="cancelled") == {c.post_id}
    with pytest.raises(ApiError):
        svc.list_posts(world, RideShareFilters(status="bogus"))


# ----------------------------------------------------------------------------- HTTP
def test_api_guest_browses_but_cannot_write(client):
    r = client.get("/api/rideshare/posts")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["data"]["posts"] == []
    body = {"mode": "cng", "origin_hub": "Farmgate", "destination_hub": "Uttara North", "seats_total": 3}
    for method, path in [("post", "/api/rideshare/posts"), ("post", "/api/rideshare/posts/x/join"),
                         ("post", "/api/rideshare/posts/x/leave"), ("post", "/api/rideshare/posts/x/cancel")]:
        r = getattr(client, method)(path, json=body)
        assert r.status_code == 401 and r.json()["errors"][0]["code"] == "UNAUTHORIZED"


def test_api_full_flow(client, engine):
    from sqlmodel import Session

    with Session(engine) as s:
        s.add(FareMatrixRow(mode="cng", from_hub="FARMGATE", to_hub="UTTARA_NORTH", road_distance_km=12.0,
                            normal_low_bdt=100, normal_high_bdt=160))
        s.commit()
        h1, h2, hadm = (headers_for(s, e) for e in (USER1, USER2, ADMIN))
    r = client.post("/api/rideshare/posts", headers=h1, json={
        "mode": "cng", "origin_hub": "Farmgate", "destination_hub": "Uttara North", "seats_total": 2, "notes": "ok"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "data", "meta", "errors"} and body["ok"] is True
    d = body["data"]
    for key in ("post_id", "mode", "origin_hub", "destination_hub", "hub_id", "created_by", "seats_total", "seats_taken",
                "seats_remaining", "status", "estimated_total_fare_min_bdt", "estimated_total_fare_max_bdt",
                "per_person_min_bdt", "per_person_max_bdt", "suggested_safe_per_person_bdt", "scenario", "created_at",
                "expires_at", "participants", "my_participation", "warnings"):
        assert key in d, key
    assert d["my_participation"] is True and d["seats_taken"] == 1
    pid = d["post_id"]

    listed = client.get("/api/rideshare/posts").json()["data"]["posts"]  # guest view
    assert [p["post_id"] for p in listed] == [pid] and listed[0]["my_participation"] is False

    j = client.post(f"/api/rideshare/posts/{pid}/join", headers=h2).json()["data"]
    assert j["status"] == "full" and j["seats_taken"] == 2 and j["suggested_safe_per_person_bdt"] == 80
    assert j["per_person_min_bdt"] == 50.0 and j["per_person_max_bdt"] == 80.0
    r = client.post(f"/api/rideshare/posts/{pid}/join", headers=hadm)
    assert r.status_code == 409 and r.json()["errors"][0]["code"] == "RIDESHARE_FULL"

    lv = client.post(f"/api/rideshare/posts/{pid}/leave", headers=h2).json()["data"]
    assert lv["status"] == "active" and lv["seats_taken"] == 1
    r = client.post(f"/api/rideshare/posts/{pid}/cancel", headers=h2)
    assert r.status_code == 403
    assert client.post(f"/api/rideshare/posts/{pid}/cancel", headers=h1).json()["data"]["status"] == "cancelled"
    assert client.get("/api/rideshare/posts").json()["data"]["posts"] == []
    assert len(client.get("/api/rideshare/posts?active_only=false").json()["data"]["posts"]) == 1

    kinds = [e["event_type"] for e in client.get("/api/events?since_id=0").json()["data"]["events"]]
    for k in ("rideshare.created", "rideshare.joined", "rideshare.left", "rideshare.cancelled"):
        assert k in kinds
    assert client.post("/api/rideshare/posts/nope/join", headers=h1).status_code == 404


def test_api_unverified_is_forbidden(client, engine):
    from sqlmodel import Session

    with Session(engine) as s:
        u = make_user(s, "nv2@dhakapath.local", verified=False)
        from app.tests.helpers import token_for
        h = {"Authorization": f"Bearer {token_for(u)}"}
    r = client.post("/api/rideshare/posts", headers=h, json={"mode": "cng", "origin_hub": "Farmgate", "seats_total": 3})
    assert r.status_code == 403 and r.json()["errors"][0]["code"] == "NOT_VERIFIED"


def test_api_validation_error_uses_envelope(client, engine):
    from sqlmodel import Session

    with Session(engine) as s:
        h = headers_for(s, USER1)
    r = client.post("/api/rideshare/posts", headers=h, json={"mode": "cng", "origin_hub": "Farmgate", "seats_total": 1})
    assert r.status_code == 422 and r.json()["ok"] is False
