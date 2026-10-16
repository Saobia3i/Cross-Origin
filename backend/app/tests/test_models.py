from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, select

from app import enums
from app.models import (
    ALL_TABLES, Hub, Report, ReportVote, RideshareJoin, RidesharePost, RouteEdge, ScenarioMultiplier, User,
    utcnow,
)
from app.schemas import ChatResponse, VoteRequest

EXPECTED_TABLES = {
    "users", "hubs", "route_edges", "fare_matrix_rows", "metro_fare_matrix", "mode_rates",
    "scenario_multipliers", "wealth_indices", "fare_rules", "reports", "report_images", "report_votes",
    "report_drafts", "simulations", "events", "chat_sessions", "chat_messages", "chat_context",
    "llm_cache", "rideshare_posts", "rideshare_joins",
}


def make_user(session, n=1):
    u = User(email=f"u{n}@x.local", name=f"U{n}", password_hash="x")
    session.add(u)
    session.commit()
    return u


def make_hub(session, hub_id="A"):
    session.add(Hub(hub_id=hub_id, name=hub_id))
    session.commit()


def make_report(session, user, report_id="r1", **kw):
    r = Report(report_id=report_id, report_type="waterlogging", severity="heavy", description="d",
               reported_by=user.id, half_life_min=60, expires_at=utcnow() + timedelta(hours=1), **kw)
    session.add(r)
    session.commit()
    return r


def test_all_tables_created(engine):
    assert set(SQLModel.metadata.tables) == EXPECTED_TABLES
    assert {m.__tablename__ for m in ALL_TABLES} == EXPECTED_TABLES
    from sqlalchemy import inspect
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_enum_values():
    assert enums.values(enums.ReportStatus) == ["draft", "pending", "verified", "rejected", "resolved", "expired"]
    assert enums.values(enums.RouteEdgeStatus) == ["active", "closed"]
    assert enums.values(enums.Direction) == ["bidirectional", "one_way", "unknown"]
    assert enums.values(enums.HubDerivedStatus) == ["normal", "delayed", "blocked"]
    assert enums.values(enums.UserRole) == ["user", "admin"]
    assert len(enums.TransitMode) == 12 and "mrt" in enums.values(enums.TransitMode)
    assert len(enums.EventType) == 19 and "report.draft.created" in enums.values(enums.EventType)
    assert enums.values(enums.Severity) == ["light", "moderate", "heavy", "critical"]
    assert len(enums.ReportType) == 8 and len(enums.ScenarioType) == 6


@pytest.mark.parametrize("status", enums.values(enums.ReportStatus))
def test_every_report_status_is_accepted(session, status):
    make_report(session, make_user(session), status=status)
    assert session.get(Report, "r1").status == status


def test_invalid_report_status_rejected(session):
    with pytest.raises(IntegrityError):
        make_report(session, make_user(session), status="approved")


@pytest.mark.parametrize("status", enums.values(enums.RouteEdgeStatus))
def test_route_edge_status_values(session, status):
    make_hub(session, "A"), make_hub(session, "B")
    session.add(RouteEdge(route_id="E", mode="mrt", origin_hub="A", destination_hub="B", status=status))
    session.commit()
    assert session.get(RouteEdge, "E").status == status
    assert session.get(RouteEdge, "E").direction == "unknown"


def test_invalid_route_edge_values_rejected(session):
    make_hub(session, "A"), make_hub(session, "B")
    for bad in ({"status": "blocked"}, {"mode": "helicopter"}, {"direction": "sideways"}):
        session.add(RouteEdge(**{"route_id": "E", "mode": "mrt", "origin_hub": "A", "destination_hub": "B", **bad}))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_route_edge_requires_existing_hubs(session):
    make_hub(session, "A")
    session.add(RouteEdge(route_id="E", mode="mrt", origin_hub="A", destination_hub="MISSING"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_route_edge_json_defaults(session):
    make_hub(session, "A"), make_hub(session, "B")
    session.add(RouteEdge(route_id="E", mode="cng", origin_hub="A", destination_hub="B"))
    session.commit()
    session.expire_all()
    e = session.get(RouteEdge, "E")
    assert e.boarding_notes_json == [] and e.geometry_json is None and e.stops_json is None
    assert (e.flood_prone, e.construction_prone, e.student_eligible) == (False, False, False)


def test_unique_vote_per_user_per_report(session):
    user = make_user(session)
    make_report(session, user)
    session.add(ReportVote(report_id="r1", user_id=user.id, value=1))
    session.commit()
    session.add(ReportVote(report_id="r1", user_id=user.id, value=-1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    other = make_user(session, 2)
    session.add(ReportVote(report_id="r1", user_id=other.id, value=-1))
    session.commit()
    assert len(session.exec(select(ReportVote)).all()) == 2


def test_vote_value_must_be_plus_or_minus_one(session):
    user = make_user(session)
    make_report(session, user)
    session.add(ReportVote(report_id="r1", user_id=user.id, value=2))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    with pytest.raises(ValueError):
        VoteRequest(value=0)
    assert VoteRequest(value=-1).value == -1


def test_email_unique(session):
    make_user(session)
    session.add(User(email="u1@x.local", name="dup", password_hash="x"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_user_role_and_status_checked(session):
    session.add(User(email="a@x.local", name="a", password_hash="x", role="superuser"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_scenario_multiplier_unique_per_mode_scenario(session):
    session.add(ScenarioMultiplier(mode="rickshaw", scenario="rain", multiplier=1.6))
    session.commit()
    session.add(ScenarioMultiplier(mode="rickshaw", scenario="rain", multiplier=2.0))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(ScenarioMultiplier(mode="cng", scenario="rain", multiplier=1.5))
    session.commit()


def test_report_defaults(session):
    r = make_report(session, make_user(session))
    assert (r.upvotes, r.downvotes, r.confidence, r.freshness) == (0, 0, 0.0, 1.0)
    assert r.status == "pending" and r.affected_route_ids_json == []
    assert not (r.edge_closed or r.needs_admin_review or r.contradiction_flag or r.admin_approved or r.affects_routing)


def test_rideshare_join_unique(session):
    user = make_user(session)
    session.add(RidesharePost(post_id="p", mode="cng", created_by=user.id, seats_total=3))
    session.commit()
    session.add(RideshareJoin(post_id="p", user_id=user.id))
    session.commit()
    session.add(RideshareJoin(post_id="p", user_id=user.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.get(RidesharePost, "p").seats_taken == 1


def test_chat_response_uses_reply_text():
    assert "reply_text" in ChatResponse.model_fields and "reply" not in ChatResponse.model_fields
