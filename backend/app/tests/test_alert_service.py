"""Alert / report service tests (fixture-seeded database).

Fixture state: reports rpt_TEST_FIXTURE_001 (pending waterlogging at MIRPUR_10 by user1) and
rpt_TEST_FIXTURE_002 (verified all_clear at FARMGATE by user2). user1..3 are verified users, admin is admin.
"""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.enums import EventType
from app.models import Event, Report, ReportDraft, ReportVote, RouteEdge, utcnow
from app.responses import ApiError
from app.schemas import ReportCreate
from app.services import alert_rules as rules
from app.services import alert_service as svc
from app.services import routing_service as rs
from app.tests.helpers import (
    ADMIN, BUS_F_U, MRT_M_F, MRT_U_M, USER1, USER2, USER3, make_user, report_payload, user_by_email,
)


def code_of(exc_info) -> str:
    return exc_info.value.code


def events(session, *types):
    wanted = {t.value if hasattr(t, "value") else t for t in types} or None
    return [e for e in session.exec(select(Event).order_by(Event.event_id)).all()
            if wanted is None or e.event_type in wanted]


def new_report(s, who=USER2, **kw):
    return svc.create_report(s, user_by_email(s, who), report_payload(**kw))


def backdate(s, report, minutes):
    """Pretend the report was created `minutes` ago (and expires accordingly)."""
    report.created_at = report.created_at - timedelta(minutes=minutes)
    report.expires_at = rules.expiry_for(report)
    s.add(report)
    s.commit()
    svc.refresh_decay(s, report)
    s.commit()
    return report


def verified_closure(s, who=USER2, **kw):
    r = new_report(s, who, **kw)
    return svc.admin_verify(s, r.report_id, user_by_email(s, ADMIN), "ok", edge_closed=True)


def closed_routes(s):
    return set(rs.build_graph(s).closed)


# ---- confidence maths ---------------------------------------------------------------------------
def make(**kw):
    now = utcnow()
    base = dict(report_id="x", report_type="waterlogging", severity="heavy", description="d", reported_by=1,
                created_at=now, half_life_min=60, expires_at=now + timedelta(hours=2), status="pending",
                affected_hub="MIRPUR_10")
    base.update(kw)
    return Report(**base)


@pytest.mark.parametrize("severity,expected", [("light", 0.25), ("moderate", 0.35), ("heavy", 0.45), ("critical", 0.55)])
def test_severity_base(severity, expected):
    now = utcnow()
    assert rules.compute_confidence(make(severity=severity, created_at=now), now) == pytest.approx(expected)


def test_confidence_formula_votes_admin_bonus_and_decay():
    now = utcnow()
    r = make(upvotes=2, downvotes=1, created_at=now)
    assert rules.compute_confidence(r, now) == pytest.approx(0.45 + 0.10 - 0.08)
    r.status = "verified"
    assert rules.compute_confidence(r, now) == pytest.approx(0.45 + 0.10 - 0.08 + 0.20)
    one_half_life = now + timedelta(minutes=60)
    assert rules.decay_factor(r, one_half_life) == pytest.approx(0.5)
    assert rules.compute_confidence(r, one_half_life) == pytest.approx(0.67 * 0.5)
    assert rules.compute_confidence(make(upvotes=50), now) == 1.0  # clamped
    assert rules.compute_confidence(make(severity="light", downvotes=9), now) == 0.0  # clamped


def test_expiry_is_two_half_lives_from_the_reference_time():
    now = utcnow()
    r = make(created_at=now, half_life_min=30)
    assert rules.expiry_for(r) == now + timedelta(minutes=60)
    r.last_verified_at = now + timedelta(minutes=10)
    assert rules.expiry_for(r) == now + timedelta(minutes=70)


def test_half_life_defaults():
    assert rules.HALF_LIFE_MIN == {"waterlogging": 60, "severe_gridlock": 30, "intersection_blockade": 30,
                                   "road_construction": 1440, "fare_surge": 45, "accident": 45, "all_clear": 60,
                                   "other": 60}


@pytest.mark.parametrize("status", ["rejected", "resolved", "expired", "draft"])
def test_inactive_statuses_have_zero_confidence(status):
    now = utcnow()
    assert rules.compute_confidence(make(status=status, severity="critical"), now) == 0.0
    assert rules.affects_routing(make(status=status, expires_at=now + timedelta(hours=1)), now) is False


def test_routing_rule_needs_verified_confidence_time_and_target():
    now = utcnow()
    ok = make(status="verified", expires_at=now + timedelta(hours=1))
    assert rules.affects_routing(ok, now)
    assert not rules.affects_routing(make(status="pending", expires_at=now + timedelta(hours=1)), now)
    assert not rules.affects_routing(make(status="verified", expires_at=now - timedelta(seconds=1)), now)
    assert not rules.affects_routing(make(status="verified", affected_hub=None, expires_at=now + timedelta(hours=1)), now)
    assert rules.affects_routing(make(status="verified", affected_hub=None, affected_route_ids_json=["R"],
                                      expires_at=now + timedelta(hours=1)), now)
    weak = make(status="verified", severity="light", downvotes=3, expires_at=now + timedelta(hours=1))
    assert rules.compute_confidence(weak, now) < 0.40 and not rules.affects_routing(weak, now)


# ---- creating reports -----------------------------------------------------------------------------
def test_verified_user_creates_pending_report(seeded):
    r = new_report(seeded, USER3)
    assert r.status == "pending" and r.admin_approved is False and r.affects_routing is False
    assert r.half_life_min == 60 and r.expires_at == r.created_at + timedelta(minutes=120)
    assert r.confidence == pytest.approx(0.45) and r.freshness == pytest.approx(1.0)
    assert r.affected_hub == "MIRPUR_10" and r.last_verified_at is None
    assert [e.event_type for e in events(seeded, EventType.report_created)][-1] == "report.created"


def test_hub_alias_and_name_are_normalised(seeded):
    assert new_report(seeded, USER3, affected_hub="Mirpur").affected_hub == "MIRPUR_10"


def test_unverified_or_disabled_users_cannot_report(seeded):
    unverified = make_user(seeded, "new@x.local", verified=False)
    with pytest.raises(ApiError) as e:
        svc.create_report(seeded, unverified, report_payload())
    assert code_of(e) == "NOT_VERIFIED"
    disabled = make_user(seeded, "off@x.local", status="disabled")
    with pytest.raises(ApiError) as e:
        svc.create_report(seeded, disabled, report_payload())
    assert code_of(e) == "ACCOUNT_DISABLED"
    assert seeded.exec(select(Report).where(Report.reported_by.in_([unverified.id, disabled.id]))).all() == []


@pytest.mark.parametrize("overrides,code", [
    ({"affected_hub": "NARNIA"}, "UNKNOWN_HUB"),
    ({"affected_route_ids": ["MRT6_MADE_UP"]}, "ROUTE_NOT_FOUND"),
    ({"report_type": "fare_surge"}, "FARE_SURGE_FIELDS_REQUIRED"),
    ({"report_type": "fare_surge", "mode": "rickshaw"}, "FARE_SURGE_FIELDS_REQUIRED"),
    ({"report_type": "fare_surge", "fare_amount_bdt": 100}, "FARE_SURGE_FIELDS_REQUIRED"),
])
def test_blocking_sanity_errors(seeded, overrides, code):
    with pytest.raises(ApiError) as e:
        new_report(seeded, USER3, **overrides)
    assert code_of(e) == code
    assert seeded.exec(select(Report).where(Report.reported_by == user_by_email(seeded, USER3).id)).all() == []


def test_incident_time_must_not_be_future_or_older_than_a_day(seeded):
    now = utcnow()
    with pytest.raises(ApiError) as e:
        new_report(seeded, USER3, incident_time=now + timedelta(minutes=5))
    assert code_of(e) == "INCIDENT_TIME_FUTURE"
    with pytest.raises(ApiError) as e:
        new_report(seeded, USER3, incident_time=now - timedelta(hours=25))
    assert code_of(e) == "INCIDENT_TIME_TOO_OLD"
    ok = new_report(seeded, USER3, incident_time=now - timedelta(hours=23))
    assert ok.incident_time < now - timedelta(hours=22)


def test_duplicate_report_is_flagged(seeded):
    first = new_report(seeded, USER3)
    second = new_report(seeded, USER3)  # same user, hub, type, seconds apart
    assert second.needs_admin_review is True and second.status == "pending"
    assert "duplicate" in second.raw_json["sanity_flags"] and first.report_id in second.raw_json["sanity"]["duplicate_of"]
    assert first.needs_admin_review is False
    assert new_report(seeded, USER2).needs_admin_review is False  # a different user is not a duplicate
    assert new_report(seeded, USER3, affected_hub="FARMGATE", affected_route_ids=[]).raw_json["sanity_flags"] == \
        ["contradiction"]  # a different place is not a duplicate (but FARMGATE has a verified all-clear)
    old = backdate(seeded, new_report(seeded, USER1, affected_hub="UTTARA_NORTH", affected_route_ids=[MRT_U_M]), 11)
    assert "duplicate" not in new_report(seeded, USER1, affected_hub="UTTARA_NORTH", affected_route_ids=[MRT_U_M]).raw_json["sanity_flags"] \
        or old is None


def test_unrealistic_fare_surge_is_flagged_not_blocked(seeded):
    base = dict(report_type="fare_surge", severity="moderate", affected_hub=None, affected_route_ids=[BUS_F_U],
                mode="rickshaw", description="Driver asked a crazy price")
    bad = new_report(seeded, USER3, fare_amount_bdt=5000, **base)
    assert bad.status == "pending" and bad.needs_admin_review and "unrealistic_fare" in bad.raw_json["sanity_flags"]
    assert bad.affects_routing is False and bad.half_life_min == 45
    assert bad.raw_json["sanity"]["fare_check"]["is_realistic"] is False
    fine = new_report(seeded, USER2, fare_amount_bdt=200, **base)
    assert fine.needs_admin_review is False and fine.raw_json["sanity_flags"] == []


def test_fare_check_is_skipped_when_distance_is_unknown(seeded):
    r = new_report(seeded, USER3, report_type="fare_surge", mode="rickshaw", fare_amount_bdt=500, affected_route_ids=[],
                   affected_hub="FARMGATE", severity="light")
    assert "unrealistic_fare" not in r.raw_json["sanity_flags"]  # nothing to compare with; noted, not guessed


def test_contradiction_with_verified_all_clear_sets_review_flags(seeded):
    r = new_report(seeded, USER3, affected_hub="FARMGATE", affected_route_ids=[])  # rpt_TEST_FIXTURE_002 is a verified all_clear
    assert r.status == "pending" and r.needs_admin_review is True and r.contradiction_flag is True
    assert r.raw_json["sanity"]["conflicts_with"] == ["rpt_TEST_FIXTURE_002"] and r.affects_routing is False


def test_all_clear_contradicts_a_fresh_verified_hazard(seeded):
    verified_closure(seeded, USER2)  # waterlogging at MIRPUR_10, verified
    clear = new_report(seeded, USER3, report_type="all_clear", severity="light")
    assert clear.contradiction_flag and clear.needs_admin_review and clear.status == "pending"


def test_all_clear_contradicts_a_verified_closure_on_the_same_route_even_if_old(seeded):
    closure = verified_closure(seeded, USER2)
    closure.last_verified_at = utcnow() - timedelta(minutes=90)  # outside the hub window, still active
    closure.expires_at = rules.expiry_for(closure)
    seeded.add(closure)
    seeded.commit()
    clear = new_report(seeded, USER3, report_type="all_clear", severity="light", affected_hub=None)
    assert clear.contradiction_flag and clear.raw_json["sanity"]["conflicts_with"] == [closure.report_id]
    far = new_report(seeded, USER1, report_type="all_clear", severity="light", affected_hub="UTTARA_NORTH",
                     affected_route_ids=[MRT_U_M])
    assert far.contradiction_flag is False


# ---- routing effects ------------------------------------------------------------------------------------------
def test_draft_does_not_affect_routing(seeded):
    svc.create_draft(seeded, "S1", user_by_email(seeded, USER2).id, report_payload())
    assert seeded.exec(select(Report).where(Report.report_id.like("rpt_%"))).all()  # only the fixture reports
    assert len(seeded.exec(select(Report)).all()) == 2
    assert closed_routes(seeded) == {BUS_F_U} and svc.get_routing_impacting_alerts(seeded) != []  # fixture all_clear only
    assert all(r.report_id == "rpt_TEST_FIXTURE_002" for r in svc.get_routing_impacting_alerts(seeded))


def test_pending_report_does_not_affect_routing(seeded):
    r = new_report(seeded, USER2)
    assert r.status == "pending" and r.affects_routing is False
    assert MRT_M_F not in closed_routes(seeded)
    assert r.report_id not in [x.report_id for x in svc.get_routing_impacting_alerts(seeded)]


def test_verified_report_affects_routing(seeded):
    r = verified_closure(seeded)
    assert r.status == "verified" and r.affects_routing is True and r.admin_approved is True
    assert r.confidence == pytest.approx(0.65) and r.last_verified_at is not None
    assert MRT_M_F in closed_routes(seeded)
    assert r.report_id in [x.report_id for x in svc.get_routing_impacting_alerts(seeded)]
    assert svc.get_routing_impacting_alerts(seeded)[-1].affects_routing


def test_verified_delay_report_adds_time_without_closing(seeded):
    r = new_report(seeded, USER2)
    svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN), extra_time_min=12)
    assert MRT_M_F not in closed_routes(seeded)
    assert rs.build_graph(seeded).delayed[MRT_M_F] == 12


def test_rejected_report_does_not_affect_routing(seeded):
    r = verified_closure(seeded)
    svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "photo shows a dry road")
    seeded.refresh(r)
    assert r.status == "rejected" and r.affects_routing is False and r.confidence == 0
    assert MRT_M_F not in closed_routes(seeded)


def test_resolved_report_restores_routing(seeded):
    r = verified_closure(seeded)
    assert MRT_M_F in closed_routes(seeded)
    svc.resolve_report(seeded, r.report_id, user_by_email(seeded, USER3))
    seeded.refresh(r)
    assert r.status == "resolved" and r.affects_routing is False and r.confidence == 0
    assert MRT_M_F not in closed_routes(seeded)
    recalcs = events(seeded, EventType.route_recalculated)
    assert [e.payload_json["reason"] for e in recalcs] == ["report_verified", "report_resolved"]


def test_expired_report_does_not_affect_routing(seeded):
    r = verified_closure(seeded)
    later = utcnow() + timedelta(hours=3)  # past 2 half-lives
    assert MRT_M_F not in set(rs.build_graph(seeded, now=later).closed)
    expired = svc.expire_old_reports(seeded, now=later)
    assert [x.report_id for x in expired if x.report_id == r.report_id]
    seeded.refresh(r)
    assert r.status == "expired" and r.affects_routing is False and r.freshness == 0 and r.confidence == 0
    assert MRT_M_F not in closed_routes(seeded)
    assert [e.event_type for e in events(seeded, EventType.report_expired, EventType.route_recalculated)][-2:] == \
        ["report.expired", "route.recalculated"]


def test_decay_below_threshold_stops_routing_effect_before_expiry(seeded):
    r = verified_closure(seeded, severity="light")  # 0.25 + 0.20 = 0.45; below 0.40 after ~10 min at 60 min half-life
    assert MRT_M_F in closed_routes(seeded)
    assert MRT_M_F not in set(rs.build_graph(seeded, now=utcnow() + timedelta(minutes=30)).closed)
    assert r.status == "verified"  # still not expired, simply too weak


def test_pending_report_expires_without_routing_event(seeded):
    r = new_report(seeded, USER2)
    svc.expire_old_reports(seeded, now=utcnow() + timedelta(hours=3))
    seeded.refresh(r)
    assert r.status == "expired"
    # the fixture's own verified all-clear expires in the same sweep (and does recalculate); this pending one does not
    assert [e.report_id for e in events(seeded, EventType.route_recalculated)] == ["rpt_TEST_FIXTURE_002"]


# ---- votes ------------------------------------------------------------------------------------------------------
def test_upvote_increases_confidence_and_refreshes_last_verified_at(seeded):
    r = backdate(seeded, new_report(seeded, USER2), 30)
    before_conf, before_fresh = r.confidence, r.freshness
    assert before_conf < 0.45 and r.last_verified_at is None
    svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER3), 1)
    seeded.refresh(r)
    assert r.upvotes == 1 and r.confidence > before_conf and r.freshness > before_fresh
    assert r.last_verified_at is not None and utcnow() - r.last_verified_at < timedelta(seconds=30)
    assert r.confidence == pytest.approx(0.50, abs=0.01) and r.community_score == 1
    assert r.expires_at == r.last_verified_at + timedelta(minutes=120)
    assert r.status == "pending"  # votes never change the status


def test_downvote_decreases_confidence_and_does_not_refresh(seeded):
    r = backdate(seeded, new_report(seeded, USER2), 30)
    before = r.confidence
    svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER3), -1)
    seeded.refresh(r)
    assert r.downvotes == 1 and r.confidence < before and r.last_verified_at is None
    assert r.community_score == -1 and r.freshness == pytest.approx(rules.decay_factor(r, utcnow()), abs=0.01)


def test_downvote_keeps_an_earlier_refresh_untouched(seeded):
    r = new_report(seeded, USER2)
    svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER3), 1)
    seeded.refresh(r)
    stamp = r.last_verified_at
    svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER1), -1)
    seeded.refresh(r)
    assert r.last_verified_at == stamp and (r.upvotes, r.downvotes) == (1, 1)


def test_reporter_cannot_vote_or_confirm_own_report(seeded):
    r = new_report(seeded, USER2)
    for call in (lambda: svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER2), 1),
                 lambda: svc.confirm_report(seeded, r.report_id, user_by_email(seeded, USER2))):
        with pytest.raises(ApiError) as e:
            call()
        assert code_of(e) == "CANNOT_VOTE_OWN_REPORT"
    assert seeded.exec(select(ReportVote)).all() == []


def test_one_vote_per_user_can_be_changed_or_retracted(seeded):
    r = new_report(seeded, USER2)
    u3 = user_by_email(seeded, USER3)
    svc.vote_report(seeded, r.report_id, u3, 1)
    svc.vote_report(seeded, r.report_id, u3, 1)  # same vote again: no second vote
    seeded.refresh(r)
    assert (r.upvotes, r.downvotes) == (1, 0) and len(seeded.exec(select(ReportVote)).all()) == 1
    assert len(events(seeded, EventType.report_voted)) == 1  # the repeat changed nothing
    svc.vote_report(seeded, r.report_id, u3, -1)  # change of mind
    seeded.refresh(r)
    assert (r.upvotes, r.downvotes) == (0, 1) and len(seeded.exec(select(ReportVote)).all()) == 1
    svc.retract_vote(seeded, r.report_id, u3)
    seeded.refresh(r)
    assert (r.upvotes, r.downvotes, r.community_score) == (0, 0, 0) and seeded.exec(select(ReportVote)).all() == []
    with pytest.raises(ApiError) as e:
        svc.retract_vote(seeded, r.report_id, u3)
    assert code_of(e) == "NO_VOTE_TO_RETRACT"


def test_invalid_vote_value(seeded):
    r = new_report(seeded, USER2)
    with pytest.raises(ApiError):
        svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER3), 2)


def test_votes_never_verify_or_reject(seeded):
    r = new_report(seeded, USER2)
    for n in range(8):
        voter = make_user(seeded, f"voter{n}@x.local")
        svc.vote_report(seeded, r.report_id, voter, 1)
    seeded.refresh(r)
    assert r.status == "pending" and r.admin_approved is False and r.affects_routing is False
    assert r.upvotes == 8 and r.confidence > 0.8
    assert MRT_M_F not in closed_routes(seeded)
    for n in range(20):
        svc.vote_report(seeded, r.report_id, make_user(seeded, f"hater{n}@x.local"), -1)
    seeded.refresh(r)
    assert r.status == "pending" and r.confidence == 0.0  # clamped, still pending


def test_strong_community_signal_asks_for_admin_review(seeded):
    r = new_report(seeded, USER2)
    assert r.needs_admin_review is False
    for n in range(3):
        svc.vote_report(seeded, r.report_id, make_user(seeded, f"v{n}@x.local"), 1)
    seeded.refresh(r)
    assert r.needs_admin_review is True and r.status == "pending"
    d = new_report(seeded, USER3, affected_hub="UTTARA_NORTH", affected_route_ids=[MRT_U_M])
    for n in range(3):
        svc.vote_report(seeded, d.report_id, make_user(seeded, f"d{n}@x.local"), -1)
    seeded.refresh(d)
    assert d.needs_admin_review is True and d.community_score == -3


def test_cannot_vote_on_closed_reports(seeded):
    r = new_report(seeded, USER2)
    svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "spam")
    with pytest.raises(ApiError) as e:
        svc.vote_report(seeded, r.report_id, user_by_email(seeded, USER3), 1)
    assert code_of(e) == "REPORT_NOT_VOTABLE"
    with pytest.raises(ApiError) as e:
        svc.vote_report(seeded, "rpt_missing", user_by_email(seeded, USER3), 1)
    assert code_of(e) == "REPORT_NOT_FOUND"


def test_confirm_is_an_upvote_that_publishes_confirmed(seeded):
    r = backdate(seeded, new_report(seeded, USER2), 20)
    svc.confirm_report(seeded, r.report_id, user_by_email(seeded, USER3))
    seeded.refresh(r)
    assert r.upvotes == 1 and r.last_verified_at is not None
    assert [e.event_type for e in events(seeded, EventType.report_confirmed, EventType.report_voted)] == ["report.confirmed"]


# ---- admin ------------------------------------------------------------------------------------------------------------
def test_admin_verify_changes_status(seeded):
    r = new_report(seeded, USER2)
    out = svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN), "confirmed on the ground")
    assert out.status == "verified" and out.admin_approved and out.last_verified_at is not None
    assert out.raw_json["admin_note"] == "confirmed on the ground" and out.raw_json["verified_by"] == user_by_email(seeded, ADMIN).id
    assert out.affects_routing is True
    types = [e.event_type for e in events(seeded)]
    assert types[-2:] == ["report.verified", "route.recalculated"]


def test_verified_with_no_hub_or_routes_does_not_trigger_recalculation(seeded):
    r = new_report(seeded, USER2, report_type="other", severity="light", affected_hub=None, affected_route_ids=[])
    out = svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert out.status == "verified" and out.affects_routing is False
    assert events(seeded, EventType.route_recalculated) == []


def test_admin_verify_acknowledges_flags(seeded):
    r = new_report(seeded, USER3, affected_hub="FARMGATE", affected_route_ids=[])
    assert r.needs_admin_review
    out = svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert out.status == "verified" and out.needs_admin_review is False and out.contradiction_flag is True
    assert out.raw_json["reviewed_flags"] == ["contradiction"] and out.raw_json["sanity_flags"] == []


def test_edge_closed_needs_routes(seeded):
    r = new_report(seeded, USER2, affected_route_ids=[])
    with pytest.raises(ApiError) as e:
        svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN), edge_closed=True)
    assert code_of(e) == "EDGE_CLOSED_REQUIRES_ROUTES"


def test_admin_reject_changes_status(seeded):
    r = new_report(seeded, USER2)
    out = svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "duplicate of another report")
    assert out.status == "rejected" and out.affects_routing is False and out.raw_json["reject_reason"]
    assert [e.event_type for e in events(seeded, EventType.report_rejected)] == ["report.rejected"]
    with pytest.raises(ApiError) as e:
        svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "again")
    assert code_of(e) == "INVALID_STATUS_TRANSITION"
    other = new_report(seeded, USER3, affected_hub="UTTARA_NORTH", affected_route_ids=[])
    with pytest.raises(ApiError):
        svc.admin_reject(seeded, other.report_id, user_by_email(seeded, ADMIN), "  ")


def test_reject_can_take_back_a_verified_report(seeded):
    r = verified_closure(seeded)
    svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "wrong place")
    assert [e.payload_json["reason"] for e in events(seeded, EventType.route_recalculated)] == \
        ["report_verified", "report_rejected"]


def test_admin_reopen(seeded):
    r = new_report(seeded, USER2)
    svc.admin_reject(seeded, r.report_id, user_by_email(seeded, ADMIN), "mistake")
    out = svc.admin_reopen(seeded, r.report_id, user_by_email(seeded, ADMIN), "evidence arrived")
    assert out.status == "pending" and out.admin_approved is False and out.affects_routing is False
    assert out.last_verified_at is not None and out.expires_at > utcnow()
    assert [e.event_type for e in events(seeded, EventType.report_reopened)] == ["report.reopened"]
    with pytest.raises(ApiError) as e:  # pending cannot be reopened
        svc.admin_reopen(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert code_of(e) == "INVALID_STATUS_TRANSITION"


def test_reopen_an_expired_report_gets_a_fresh_lifetime(seeded):
    r = new_report(seeded, USER2)
    svc.expire_old_reports(seeded, now=utcnow() + timedelta(hours=3))
    out = svc.admin_reopen(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert out.status == "pending" and out.expires_at > utcnow() + timedelta(hours=1)


def test_verify_requires_pending_and_admin(seeded):
    r = new_report(seeded, USER2)
    with pytest.raises(ApiError) as e:
        svc.admin_verify(seeded, r.report_id, user_by_email(seeded, USER1))
    assert code_of(e) == "FORBIDDEN"
    svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    with pytest.raises(ApiError) as e:
        svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert code_of(e) == "INVALID_STATUS_TRANSITION"


def test_resolve_needs_a_verified_report(seeded):
    r = new_report(seeded, USER2)
    with pytest.raises(ApiError) as e:
        svc.resolve_report(seeded, r.report_id, user_by_email(seeded, USER3))
    assert code_of(e) == "INVALID_STATUS_TRANSITION"
    svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    assert svc.resolve_report(seeded, r.report_id, user_by_email(seeded, ADMIN)).status == "resolved"


def test_resolving_a_report_with_no_routing_effect_publishes_no_recalculation(seeded):
    r = new_report(seeded, USER2, report_type="other", severity="light", affected_hub=None, affected_route_ids=[])
    svc.admin_verify(seeded, r.report_id, user_by_email(seeded, ADMIN))
    svc.resolve_report(seeded, r.report_id, user_by_email(seeded, USER3))
    assert events(seeded, EventType.route_recalculated) == []


# ---- drafts ------------------------------------------------------------------------------------------------------------
def test_draft_flow_publishes_events_and_creates_pending_report(seeded):
    u = user_by_email(seeded, USER2)
    draft = svc.create_draft(seeded, "CHAT1", u.id, report_payload())
    assert draft.status == "draft" and draft.payload_json["report_type"] == "waterlogging"
    assert len(seeded.exec(select(Report)).all()) == 2  # no report yet
    report = svc.submit_draft(seeded, draft.draft_id, u)
    seeded.refresh(draft)
    assert report.status == "pending" and draft.status == "submitted" and draft.submitted_at
    assert report.created_by_session == "CHAT1" and report.raw_json["draft_id"] == draft.draft_id
    mine = [e.event_type for e in events(seeded) if e.session_id == "CHAT1" or e.report_id == report.report_id]
    assert mine == ["report.draft.created", "report.draft.submitted", "report.created"]
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, u)
    assert code_of(e) == "DRAFT_NOT_OPEN"


def test_draft_cancel(seeded):
    u = user_by_email(seeded, USER2)
    draft = svc.create_draft(seeded, "CHAT1", u.id, report_payload())
    assert svc.cancel_draft(seeded, draft.draft_id, u).status == "cancelled"
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, u)
    assert code_of(e) == "DRAFT_NOT_OPEN"


def test_draft_belongs_to_its_user_but_a_guest_draft_can_be_claimed(seeded):
    draft = svc.create_draft(seeded, "CHAT1", user_by_email(seeded, USER2).id, report_payload())
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, user_by_email(seeded, USER3))
    assert code_of(e) == "DRAFT_FORBIDDEN"
    guest = svc.create_draft(seeded, "CHAT2", None, report_payload(affected_hub="UTTARA_NORTH"))
    report = svc.submit_draft(seeded, guest.draft_id, user_by_email(seeded, USER3))
    assert report.reported_by == user_by_email(seeded, USER3).id
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, "drf_nope", user_by_email(seeded, USER3))
    assert code_of(e) == "DRAFT_NOT_FOUND"


def test_submit_validates_the_payload_and_keeps_the_draft_open(seeded):
    u = user_by_email(seeded, USER2)
    draft = svc.create_draft(seeded, "CHAT1", u.id, report_payload(affected_hub="NOWHERE"))
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, u)
    assert code_of(e) == "UNKNOWN_HUB"
    seeded.refresh(draft)
    assert draft.status == "draft" and "report.draft.submitted" not in [x.event_type for x in events(seeded)]
    assert len(seeded.exec(select(Report)).all()) == 2


def test_old_drafts_expire(seeded):
    u = user_by_email(seeded, USER2)
    draft = svc.create_draft(seeded, "CHAT1", u.id, report_payload())
    draft.created_at = utcnow() - timedelta(hours=25)
    seeded.add(draft)
    seeded.commit()
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, u)
    assert code_of(e) == "DRAFT_EXPIRED"
    seeded.refresh(draft)
    assert draft.status == "expired"


def test_unverified_user_cannot_submit_a_draft(seeded):
    draft = svc.create_draft(seeded, "CHAT1", None, report_payload())
    with pytest.raises(ApiError) as e:
        svc.submit_draft(seeded, draft.draft_id, make_user(seeded, "nv@x.local", verified=False))
    assert code_of(e) == "NOT_VERIFIED"


# ---- images --------------------------------------------------------------------------------------------------------------
def test_report_with_images_stores_them(seeded, uploads):
    from app.tests.helpers import jpeg_bytes, png_bytes, webp_bytes

    imgs = [svc.ImageUpload("a.png", "image/png", png_bytes()), svc.ImageUpload("b.jpg", "image/jpeg", jpeg_bytes()),
            svc.ImageUpload("c.webp", "image/webp", webp_bytes())]
    r = svc.create_report(seeded, user_by_email(seeded, USER2), report_payload(), images=imgs)
    folder = uploads / "reports" / r.report_id
    assert sorted(p.suffix for p in folder.iterdir()) == [".jpg", ".png", ".webp"]
    from app.models import ReportImage
    rows = seeded.exec(select(ReportImage).where(ReportImage.report_id == r.report_id)).all()
    assert len(rows) == 3 and {x.mime for x in rows} == {"image/png", "image/jpeg", "image/webp"}


@pytest.mark.parametrize("img,code", [
    (svc.ImageUpload("x.gif", "image/gif", b"GIF89a"), "IMAGE_TYPE_NOT_ALLOWED"),
    (svc.ImageUpload("x.png", "image/png", b"not a png"), "IMAGE_INVALID"),
    (svc.ImageUpload("big.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)), "IMAGE_TOO_LARGE"),
])
def test_bad_images_are_rejected_and_no_report_is_created(seeded, uploads, img, code):
    with pytest.raises(ApiError) as e:
        svc.create_report(seeded, user_by_email(seeded, USER2), report_payload(), images=[img])
    assert code_of(e) == code
    assert len(seeded.exec(select(Report)).all()) == 2 and not (uploads / "reports").exists()


def test_too_many_images(seeded, uploads):
    from app.tests.helpers import png_bytes

    with pytest.raises(ApiError) as e:
        svc.create_report(seeded, user_by_email(seeded, USER2), report_payload(),
                          images=[svc.ImageUpload(f"{n}.png", "image/png", png_bytes()) for n in range(6)])
    assert code_of(e) == "IMAGE_TOO_MANY"


def test_text_only_reports_need_no_images(seeded, uploads):
    r = new_report(seeded, USER2)
    assert not (uploads / "reports" / r.report_id).exists()


# ---- misc --------------------------------------------------------------------------------------------------------------------
def test_refresh_decay_updates_derived_fields_and_expires(seeded):
    r = new_report(seeded, USER2)
    r.created_at = utcnow() - timedelta(minutes=60)  # one half-life
    seeded.add(r)
    seeded.flush()
    svc.refresh_decay(seeded, r)
    assert r.freshness == pytest.approx(0.5, abs=0.01) and r.confidence == pytest.approx(0.225, abs=0.01)
    r.expires_at = utcnow() - timedelta(seconds=1)
    svc.refresh_decay(seeded, r)
    assert r.status == "expired"


def test_run_sanity_checks_directly(seeded):
    res = svc.run_sanity_checks(seeded, ReportCreate(**report_payload(affected_hub="Mirpur")), user_by_email(seeded, USER3))
    assert res.ok and res.resolved_hub_id == "MIRPUR_10" and res.flags == []
    bad = svc.run_sanity_checks(seeded, report_payload(affected_hub="Nope", affected_route_ids=["zzz"]), user_by_email(seeded, USER3))
    assert not bad.ok and {e["code"] for e in bad.errors} == {"UNKNOWN_HUB", "ROUTE_NOT_FOUND"}


def test_no_report_is_invented_in_production_style_seed(session, tmp_path):
    from app.services.seeder import seed

    seed(session, "normalized", tmp_path)  # no normalized files at all
    assert session.exec(select(Report)).all() == []
