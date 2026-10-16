"""Event log: publishing, cursor polling, session visibility, debug publish."""
import pytest
from sqlmodel import Session, select

from app.config import settings
from app.enums import EventType, values
from app.models import Event
from app.responses import ApiError
from app.services import alert_service as svc
from app.services import event_service as es
from app.tests.helpers import ADMIN, USER2, USER3, headers_for, report_payload, user_by_email


def ids(events):
    return [e.event_id for e in events]


def test_publish_event_stores_type_payload_and_links(session):
    e = es.publish_event(session, EventType.report_created, {"a": 1}, session_id="S", report_id="r1", route_id="R")
    session.commit()
    stored = session.get(Event, e.event_id)
    assert (stored.event_type, stored.payload_json, stored.session_id, stored.report_id, stored.route_id) == \
        ("report.created", {"a": 1}, "S", "r1", "R")
    assert stored.created_at is not None and e.event_id == 1
    assert es.publish_event(session, "route.recalculated").event_type == "route.recalculated"  # plain string too


def test_event_ids_increase_and_unknown_types_are_rejected(session):
    a = es.publish_event(session, EventType.report_voted)
    b = es.publish_event(session, EventType.report_voted)
    assert b.event_id == a.event_id + 1
    with pytest.raises(ApiError) as e:
        es.publish_event(session, "report.exploded")
    assert e.value.code == "INVALID_EVENT_TYPE"


def test_every_contract_event_type_can_be_published(session):
    for t in values(EventType):
        es.publish_event(session, t)
    session.commit()
    assert len(es.get_events(session, limit=100)) == len(values(EventType)) == 19


def test_get_events_since_id_cursor(session):
    made = [es.publish_event(session, EventType.report_voted, {"n": n}) for n in range(5)]
    session.commit()
    all_ids = ids(made)
    assert ids(es.get_events(session)) == all_ids
    assert ids(es.get_events(session, since_id=all_ids[1])) == all_ids[2:]
    assert es.get_events(session, since_id=all_ids[-1]) == []
    page = es.get_events(session, since_id=0, limit=2)
    assert ids(page) == all_ids[:2]
    assert ids(es.get_events(session, since_id=page[-1].event_id, limit=2)) == all_ids[2:4]  # follow the cursor
    assert ids(es.get_events(session, since_id=-5)) == all_ids  # negative cursor treated as 0
    assert len(es.get_events(session, limit=0)) == 1 and len(es.get_events(session, limit=10_000)) == 5  # clamped


def test_session_scoped_events_are_only_visible_to_their_session(session):
    g = es.publish_event(session, EventType.report_created)
    a = es.publish_event(session, EventType.report_draft_created, session_id="A")
    b = es.publish_event(session, EventType.report_draft_created, session_id="B")
    session.commit()
    assert ids(es.get_events(session)) == [g.event_id]
    assert ids(es.get_events(session, session_id="A")) == [g.event_id, a.event_id]
    assert ids(es.get_events(session, session_id="B")) == [g.event_id, b.event_id]


def test_filter_by_type(session):
    es.publish_event(session, EventType.report_voted)
    es.publish_event(session, EventType.report_created)
    session.commit()
    assert [e.event_type for e in es.get_events(session, event_type="report.created")] == ["report.created"]


def test_events_are_rolled_back_with_a_failed_change(seeded):
    before = len(seeded.exec(select(Event)).all())
    with pytest.raises(ApiError):
        svc.create_report(seeded, user_by_email(seeded, USER2), report_payload(affected_hub="NOWHERE"))
    assert len(seeded.exec(select(Event)).all()) == before


def test_service_level_publish_and_get_wrappers(seeded):
    e = svc.publish_event(seeded, EventType.simulation_applied, {"x": 1}, session_id="S9")
    seeded.commit()
    assert svc.get_events(seeded, since_id=e.event_id - 1, session_id="S9")[0].event_id == e.event_id
    assert svc.get_events(seeded, since_id=e.event_id - 1) == []  # a session event is hidden without the session


def test_report_lifecycle_publishes_the_contract_events(seeded):
    admin, u2, u3 = (user_by_email(seeded, x) for x in (ADMIN, USER2, USER3))
    r = svc.create_report(seeded, u2, report_payload())
    svc.vote_report(seeded, r.report_id, u3, 1)
    svc.confirm_report(seeded, r.report_id, user_by_email(seeded, "user1@dhakapath.local"))
    svc.admin_verify(seeded, r.report_id, admin, edge_closed=True)
    svc.resolve_report(seeded, r.report_id, u3)
    svc.admin_reopen(seeded, r.report_id, admin)
    svc.admin_reject(seeded, r.report_id, admin, "no")
    svc.admin_reopen(seeded, r.report_id, admin)
    svc.expire_old_reports(seeded, now=svc.utcnow().replace(year=2099))
    mine = [e.event_type for e in es.get_events(seeded, limit=500) if e.report_id == r.report_id]
    assert mine == ["report.created", "report.voted", "report.confirmed", "report.verified", "route.recalculated",
                    "report.resolved", "route.recalculated", "report.reopened", "report.rejected", "report.reopened",
                    "report.expired"]
    payload = next(e for e in es.get_events(seeded, limit=500) if e.event_type == "report.verified").payload_json
    assert payload["status"] == "verified" and payload["edge_closed"] is True and payload["report_id"] == r.report_id
    assert "email" not in str(payload)  # no personal data in event payloads


# ---- HTTP ----------------------------------------------------------------------------------------------
def get_events(client, **params):
    r = client.get("/api/events", params=params)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    return body["data"]


def test_api_events_returns_cursor(client, engine):
    with Session(engine) as s:
        for n in range(5):
            es.publish_event(s, EventType.report_voted, {"n": n})
        s.commit()
    first = get_events(client, limit=2)
    assert len(first["events"]) == 2 and first["has_more"] is True
    assert first["next_since_id"] == first["events"][-1]["event_id"]
    second = get_events(client, since_id=first["next_since_id"], limit=100)
    assert second["events"][0]["event_id"] == first["next_since_id"] + 1
    assert second["has_more"] is False
    last = get_events(client, since_id=second["next_since_id"])
    assert last["events"] == [] and last["has_more"] is False and last["next_since_id"] == second["next_since_id"]
    assert set(first["events"][0]) == {"event_id", "event_type", "session_id", "report_id", "route_id", "payload", "created_at"}


def test_api_events_session_and_type_filters(client, engine):
    with Session(engine) as s:
        es.publish_event(s, EventType.report_draft_created, {"d": 1}, session_id="MINE")
        es.publish_event(s, EventType.report_voted)
        s.commit()
    public = get_events(client)["events"]
    assert all(e["session_id"] is None for e in public)
    mine = get_events(client, session_id="MINE")["events"]
    assert {"report.draft.created"} <= {e["event_type"] for e in mine}
    only = get_events(client, event_type="report.voted")["events"]
    assert {e["event_type"] for e in only} == {"report.voted"}
    assert client.get("/api/events", params={"event_type": "nope"}).status_code == 422
    assert client.get("/api/events", params={"since_id": -1}).status_code == 422
    assert client.get("/api/events", params={"limit": 0}).status_code == 422


def test_report_actions_show_up_in_the_feed(client, engine):
    with Session(engine) as s:
        user, admin = headers_for(s, USER2), headers_for(s, ADMIN)
    cursor = get_events(client)["next_since_id"]
    rid = client.post("/api/reports", json=report_payload(), headers=user).json()["data"]["report"]["report_id"]
    client.post(f"/api/admin/reports/{rid}/verify", json={"edge_closed": True}, headers=admin)
    feed = get_events(client, since_id=cursor)["events"]
    assert [e["event_type"] for e in feed] == ["report.created", "report.verified", "route.recalculated"]
    assert all(e["report_id"] == rid for e in feed)


# ---- publish-debug -----------------------------------------------------------------------------------------
def debug_body(**kw):
    return {"event_type": "route.recalculated", "payload": {"why": "demo"}, **kw}


def test_publish_debug_needs_the_secret(client):
    assert client.post("/api/events/publish-debug", json=debug_body()).status_code == 403
    r = client.post("/api/events/publish-debug", json=debug_body(), headers={"X-Demo-Secret": "wrong"})
    assert r.status_code == 403 and r.json()["errors"][0]["code"] == "DEBUG_SECRET_INVALID"
    r = client.post("/api/events/publish-debug", json=debug_body(secret="wrong"))
    assert r.status_code == 403
    assert get_events(client)["events"] == []


def test_publish_debug_with_header_or_body_secret(client):
    r = client.post("/api/events/publish-debug", json=debug_body(), headers={"X-Demo-Secret": settings.demo_secret})
    assert r.status_code == 201 and r.json()["ok"] is True
    event = r.json()["data"]["event"]
    assert event["event_type"] == "route.recalculated" and event["payload"] == {"why": "demo", "debug": True}
    r2 = client.post("/api/events/publish-debug", json=debug_body(secret=settings.demo_secret, session_id="S1"))
    assert r2.status_code == 201
    assert [e["event_id"] for e in get_events(client)["events"]] == [event["event_id"]]  # the S1 one is private


def test_publish_debug_only_inserts_events(client, engine):
    from app.models import Report, RouteEdge

    with Session(engine) as s:
        before = ([(r.report_id, r.status) for r in s.exec(select(Report)).all()],
                  [(e.route_id, e.status) for e in s.exec(select(RouteEdge)).all()])
    client.post("/api/events/publish-debug", headers={"X-Demo-Secret": settings.demo_secret},
                json=debug_body(route_id="MRT6_MIRPUR_10_FARMGATE", report_id="rpt_TEST_FIXTURE_001"))
    with Session(engine) as s:
        after = ([(r.report_id, r.status) for r in s.exec(select(Report)).all()],
                 [(e.route_id, e.status) for e in s.exec(select(RouteEdge)).all()])
    assert before == after
    assert client.get("/api/transit/routes").json()["data"][0]["status"] in ("active", "closed")
    bad = client.post("/api/events/publish-debug", headers={"X-Demo-Secret": settings.demo_secret},
                      json={"event_type": "route.closed.forever"})
    assert bad.status_code == 422
