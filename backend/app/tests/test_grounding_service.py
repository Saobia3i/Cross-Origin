"""Grounding: AI payloads must match the database and the fare / routing engines, or be blocked."""
import copy

import pytest
from sqlmodel import Session

from app.config import settings
from app.models import FareMatrixRow, Hub, RouteEdge
from app.services import alert_service as svc
from app.services import grounding_service as gs
from app.services import routing_service as rs
from app.tests.helpers import (
    ADMIN, MRT_M_F, MRT_U_M, USER1, USER2, USER3, make_user, report_payload, user_by_email,
)

RICK = "RICKSHAW_FARMGATE_UTTARA_NORTH"
BUS = "LOCAL_BUS_UTTARA_NORTH_FARMGATE"
H = {"X-Demo-Secret": settings.demo_secret}


@pytest.fixture
def world(seeded):
    """Fixture network + an open bus (30 BDT, 70 min) and a rickshaw range (100-150 BDT, 30 min); metro times 10/12."""
    s = seeded
    s.add(Hub(hub_id="ISLAND", name="Island", latitude=0.5, longitude=0.5))
    s.add(RouteEdge(route_id=RICK, mode="rickshaw", origin_hub="FARMGATE", destination_hub="UTTARA_NORTH", direction="unknown",
                    source_dataset="dhaka_transport_fares", road_distance_km=5.0, time_normal_min=30))
    s.add(RouteEdge(route_id=BUS, mode="local_bus", origin_hub="UTTARA_NORTH", destination_hub="FARMGATE",
                    direction="bidirectional", road_distance_km=9.0, time_normal_min=70))
    s.add(FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=100, normal_high_bdt=150))
    s.add(FareMatrixRow(mode="local_bus", from_hub="UTTARA_NORTH", to_hub="FARMGATE", normal_low_bdt=30, normal_high_bdt=30))
    s.get(RouteEdge, MRT_U_M).time_normal_min = 10
    s.get(RouteEdge, MRT_M_F).time_normal_min = 12
    s.commit()
    return s


def leg(route_id, mode, a, b, lo=None, hi=None, slo=None, shi=None, t=None, **kw):
    d = {"route_id": route_id, "mode": mode, "from_hub": a, "to_hub": b}
    for k, v in (("regular_fare_min_bdt", lo), ("regular_fare_max_bdt", hi), ("student_fare_min_bdt", slo),
                 ("student_fare_max_bdt", shi), ("travel_time_min", t)):
        if v is not None:
            d[k] = v
    return {**d, **kw}


def codes(result):
    return [v.code for v in result.violations]


def engine_option(s, a, b, **kw):
    return rs.search_routes(s, a, b, **kw)["routes"]


def rickshaw_option(s):
    return next(o for o in engine_option(s, "Uttara North", "Farmgate", avoid_modes=["mrt", "local_bus"]))


# ---- valid ---------------------------------------------------------------------------------------------
def test_valid_engine_routes_pass(world):
    options = engine_option(world, "Uttara North", "Farmgate")
    assert len(options) >= 2
    for o in options:
        r = gs.validate_ai_route_option(world, o)
        assert r.grounded and r.safe_for_display and r.violations == [], (o["route_option_id"], r.violations)
        assert r.fare_check_passed and r.duration_check_passed and r.fallback_used is False
        assert set(r.checked_route_ids) == {l["route_id"] for l in o["legs"]}
        assert {"UTTARA_NORTH", "FARMGATE"} <= set(r.checked_hub_ids)


def test_valid_route_payload_with_minimal_claims(world):
    payload = {"hub_ids": ["FARMGATE", "UTTARA_NORTH"], "legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 100, 150, t=30)],
               "total_regular_fare_min_bdt": 100, "total_regular_fare_max_bdt": 150, "total_duration_min": 30}
    r = gs.validate_route_payload(world, payload)
    assert r.grounded and r.checked_route_ids == [RICK]
    assert gs.validate_route_payload(world, {"route_option": payload}).grounded  # nested form


def test_claimed_range_inside_the_calculated_range_is_fine(world):
    assert gs.validate_route_payload(world, {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 110, 140)]}).grounded


def test_route_without_claims_only_needs_to_exist(world):
    assert gs.validate_route_payload(world, {"route_ids": [RICK]}).grounded


def test_metro_matches_official_matrix(world):
    legs = [leg(MRT_U_M, "mrt", "UTTARA_NORTH", "MIRPUR_10", t=10), leg(MRT_M_F, "mrt", "MIRPUR_10", "FARMGATE", t=12)]
    ok = {"legs": legs, "total_regular_fare_min_bdt": 22, "total_regular_fare_max_bdt": 22, "total_duration_min": 22}
    assert gs.validate_route_payload(world, ok).grounded  # one ride, matrix U-F = 22 (not 11 + 33)
    bad = copy.deepcopy(ok)
    bad["total_regular_fare_min_bdt"] = bad["total_regular_fare_max_bdt"] = 44
    r = gs.validate_route_payload(world, bad)
    assert codes(r) == ["FARE_MISMATCH"] and "official metro fare" in r.violations[0].message


# ---- hubs & routes -----------------------------------------------------------------------------------------
def test_unknown_route_id_fails(world):
    r = gs.validate_route_payload(world, {"legs": [leg("MRT6_MADE_UP", "mrt", "FARMGATE", "UTTARA_NORTH")]})
    assert not r.grounded and not r.safe_for_display and "UNKNOWN_ROUTE_ID" in codes(r)
    v = next(v for v in r.violations if v.code == "UNKNOWN_ROUTE_ID")
    assert v.offending_value == "MRT6_MADE_UP" and v.field == "legs[0].route_id"
    assert r.checked_route_ids == ["MRT6_MADE_UP"]


def test_unknown_hub_fails(world):
    r = gs.validate_route_payload(world, {"hub_ids": ["MIRPUR_10", "ATLANTIS"], "legs": [leg(MRT_M_F, "mrt", "MIRPUR_10", "ATLANTIS")]})
    assert "UNKNOWN_HUB" in codes(r) and all(v.offending_value == "ATLANTIS" for v in r.violations if v.code == "UNKNOWN_HUB")
    assert gs.validate_route_payload(world, {"origin": "Narnia", "route_ids": [RICK]}).violations[0].code == "UNKNOWN_HUB"
    assert gs.validate_route_payload(world, {"origin": "Mirpur", "route_ids": [MRT_M_F]}).grounded  # alias is a known hub


def test_leg_that_does_not_connect_the_stated_hubs_fails(world):
    r = gs.validate_route_payload(world, {"legs": [leg(MRT_M_F, "mrt", "UTTARA_NORTH", "FARMGATE")]})
    assert "UNKNOWN_ROUTE_ID" in codes(r) and "connects MIRPUR_10 and FARMGATE" in r.violations[0].message


def test_disconnected_legs_fail(world):
    r = gs.validate_route_payload(world, {"legs": [leg(MRT_U_M, "mrt", "UTTARA_NORTH", "MIRPUR_10"),
                                                   leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH")]})
    assert codes(r) == ["DISCONNECTED_LEGS"] and r.violations[0].offending_value == ["MIRPUR_10", "FARMGATE"]


def test_journey_endpoints_must_match_first_and_last_leg(world):
    r = gs.validate_route_payload(world, {"origin": "Mirpur 10", "destination": "Farmgate",
                                          "legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH")]})
    assert codes(r).count("DISCONNECTED_LEGS") == 2


def test_hallucinated_direct_route_between_unlinked_hubs(world):
    r = gs.validate_route_payload(world, {"legs": [leg("EXPRESS_FARMGATE_ISLAND", "local_bus", "FARMGATE", "ISLAND", 10, 10)]})
    assert {"UNKNOWN_ROUTE_ID", "HALLUCINATED_DIRECT_ROUTE", "FARE_MISMATCH"} <= set(codes(r))
    v = next(v for v in r.violations if v.code == "HALLUCINATED_DIRECT_ROUTE")
    assert v.offending_value == "FARMGATE->ISLAND"
    # a made-up id between hubs that DO have a real direct route is an unknown id, not a hallucinated direct route
    r2 = gs.validate_route_payload(world, {"legs": [leg("EXPRESS_UTTARA_FARMGATE", "local_bus", "UTTARA_NORTH", "FARMGATE")]})
    assert codes(r2) == ["UNKNOWN_ROUTE_ID"]


def test_unknown_line_name_fails(world):
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", line_name="Super Express Bus 9")]})
    assert codes(r) == ["UNKNOWN_ROUTE_ID"] and "Super Express" in r.violations[0].message


def test_closed_or_wrong_direction_route_is_not_available(world, monkeypatch):
    monkeypatch.setattr("app.services.routing_service.ASSUME_REVERSE_UNKNOWN", False)
    r = gs.validate_route_payload(world, {"legs": [leg("LOCAL_BUS_FARMGATE_UTTARA_NORTH", "local_bus", "FARMGATE", "UTTARA_NORTH")]})
    assert codes(r) == ["ROUTE_NOT_AVAILABLE"]  # closed by the dataset
    r = gs.validate_route_payload(world, {"legs": [leg(MRT_M_F, "mrt", "FARMGATE", "MIRPUR_10")]})  # forward-only edge
    assert codes(r) == ["ROUTE_NOT_AVAILABLE"]


# ---- fares ---------------------------------------------------------------------------------------------------
def test_fare_mismatch_fails(world):
    o = rickshaw_option(world)
    o["legs"][0]["regular_fare_max_bdt"] = 400
    o["total_regular_fare_max_bdt"] = 400
    r = gs.validate_ai_route_option(world, o)
    assert not r.grounded and not r.fare_check_passed and set(codes(r)) == {"FARE_MISMATCH"}
    low = rickshaw_option(world)
    low["total_regular_fare_min_bdt"] = 20
    assert "FARE_MISMATCH" in codes(gs.validate_ai_route_option(world, low))


def test_rickshaw_exact_fake_fare_fails(world):
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 130, 130)]})
    assert codes(r) == ["FARE_MISMATCH"] and "single exact fare" in r.violations[0].message
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 130)]})  # one number only
    assert codes(r) == ["FARE_MISMATCH"]
    ok = gs.validate_route_payload(world, {"legs": [leg(BUS, "local_bus", "UTTARA_NORTH", "FARMGATE", 30, 30)]})
    assert ok.grounded  # a fixed (non-range) fare may be exact


def test_scalar_fare_claim_must_be_inside_the_range(world):
    assert gs.validate_route_payload(world, {"route_ids": [RICK], "claimed_fare_bdt": 120}).grounded
    r = gs.validate_route_payload(world, {"route_ids": [RICK], "claimed_fare_bdt": 90})
    assert codes(r) == ["FARE_MISMATCH"] and r.violations[0].offending_value == 90


def test_student_fare_rules(world):
    ok = {"legs": [leg(BUS, "local_bus", "UTTARA_NORTH", "FARMGATE", 30, 30, 15, 15)]}   # 50% of 30
    assert gs.validate_route_payload(world, ok).grounded
    bad_bus = {"legs": [leg(BUS, "local_bus", "UTTARA_NORTH", "FARMGATE", 30, 30, 5, 5)]}
    r = gs.validate_route_payload(world, bad_bus)
    assert codes(r) == ["INVALID_STUDENT_RULE"] and not r.fare_check_passed and "50% off" in r.violations[0].message
    floor = {"legs": [leg(BUS, "local_bus", "UTTARA_NORTH", "FARMGATE", 30, 30, 20, 20)]}
    assert codes(gs.validate_route_payload(world, floor)) == ["INVALID_STUDENT_RULE"]
    rick = {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 100, 150, 50, 75)]}  # rickshaw has no student discount
    r = gs.validate_route_payload(world, rick)
    assert codes(r) == ["INVALID_STUDENT_RULE"] and "no student discount applies to rickshaw" in r.violations[0].message
    same = {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", 100, 150, 100, 150)]}
    assert gs.validate_route_payload(world, same).grounded
    metro = {"legs": [leg(MRT_M_F, "mrt", "MIRPUR_10", "FARMGATE", 33, 33, 16, 16)]}
    assert codes(gs.validate_route_payload(world, metro)) == ["INVALID_STUDENT_RULE"]


def test_student_totals_are_checked(world):
    o = engine_option(world, "Uttara North", "Farmgate", prefer_student_fare=True, avoid_modes=["mrt", "rickshaw"])[0]
    assert gs.validate_ai_route_option(world, o).grounded
    o["total_student_fare_min_bdt"] = o["total_student_fare_max_bdt"] = 1
    assert "INVALID_STUDENT_RULE" in codes(gs.validate_ai_route_option(world, o))


def test_walk_fare_must_be_zero_and_modes_canonical(world):
    world.add(RouteEdge(route_id="WALK_X", mode="walk", origin_hub="MIRPUR_10", destination_hub="FARMGATE",
                        direction="bidirectional", time_normal_min=15))
    world.commit()
    assert gs.validate_route_payload(world, {"legs": [leg("WALK_X", "walk", "MIRPUR_10", "FARMGATE", 0, 0, t=15)]}).grounded
    r = gs.validate_route_payload(world, {"legs": [leg("WALK_X", "walk", "MIRPUR_10", "FARMGATE", 10, 10)]})
    assert "FARE_MISMATCH" in codes(r)
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "hoverboard", "FARMGATE", "UTTARA_NORTH")]})
    assert codes(r) == ["UNKNOWN_MODE"]
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "local_bus", "FARMGATE", "UTTARA_NORTH")]})  # real mode, wrong route
    assert codes(r) == ["UNKNOWN_MODE"]


def test_unpriced_leg_cannot_carry_a_fare(world):
    world.add(Hub(hub_id="LONELY", name="Lonely", latitude=0.9, longitude=0.9))
    world.add(RouteEdge(route_id="MRT_LONELY", mode="mrt", origin_hub="LONELY", destination_hub="FARMGATE", direction="bidirectional"))
    world.commit()
    assert gs.validate_route_payload(world, {"legs": [leg("MRT_LONELY", "mrt", "LONELY", "FARMGATE")]}).grounded
    r = gs.validate_route_payload(world, {"legs": [leg("MRT_LONELY", "mrt", "LONELY", "FARMGATE", 40, 40)]})
    assert codes(r) == ["FARE_MISMATCH"] and "no fare" in r.violations[0].message


# ---- durations ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("claimed,ok", [(30, True), (32, True), (28, True), (33, False), (10, False), (1, False)])
def test_duration_tolerance_is_two_minutes_or_five_percent(world, claimed, ok):
    r = gs.validate_route_payload(world, {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", t=claimed)],
                                          "total_duration_min": claimed})
    assert r.grounded is ok
    if not ok:
        assert set(codes(r)) == {"DURATION_MISMATCH"} and not r.duration_check_passed and r.fare_check_passed


def test_tolerance_scales_with_long_trips(world):
    assert gs.duration_ok(73, 70) and gs.duration_ok(66.5, 70) and not gs.duration_ok(74, 70)
    assert gs.duration_ok(31.9, 30) and not gs.duration_ok(32.1, 30) and not gs.duration_ok(5, None)


def test_ai_must_not_invent_a_time_the_dataset_lacks(world):
    world.get(RouteEdge, MRT_U_M).time_normal_min = None
    world.commit()
    r = gs.validate_route_payload(world, {"legs": [leg(MRT_U_M, "mrt", "UTTARA_NORTH", "MIRPUR_10", t=10)]})
    assert codes(r) == ["DURATION_MISMATCH"] and "no travel time" in r.violations[0].message
    assert gs.validate_route_payload(world, {"legs": [leg(MRT_U_M, "mrt", "UTTARA_NORTH", "MIRPUR_10")]}).grounded
    r = gs.validate_route_payload(world, {"legs": [leg(MRT_U_M, "mrt", "UTTARA_NORTH", "MIRPUR_10"),
                                                   leg(MRT_M_F, "mrt", "MIRPUR_10", "FARMGATE")], "total_duration_min": 22})
    assert codes(r) == ["DURATION_MISMATCH"]


def test_session_scenario_changes_what_is_grounded(world):
    world.get(RouteEdge, RICK).time_rain_min = 45
    world.add(FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=1, normal_high_bdt=2))
    world.commit()
    claim = {"legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH", t=45)]}
    assert not gs.validate_route_payload(world, claim).grounded            # normal time is 30
    from app.services import simulation_service as sims
    sims.apply_simulation(world, "A", "heavy_rain")
    assert gs.validate_route_payload(world, claim, session_id="A").grounded  # this session's rain time is 45
    assert not gs.validate_route_payload(world, claim, session_id="B").grounded


# ---- alerts --------------------------------------------------------------------------------------------------------------
def test_alert_checks(world):
    pending = svc.create_report(world, user_by_email(world, USER2), report_payload())
    verified = svc.admin_verify(world, svc.create_report(world, user_by_email(world, USER3), report_payload(
        affected_hub="UTTARA_NORTH", affected_route_ids=[MRT_U_M])).report_id, user_by_email(world, ADMIN), edge_closed=True)
    base = {"route_ids": [RICK]}
    assert gs.validate_route_payload(world, {**base, "alert_ids": [pending.report_id, verified.report_id]}).grounded
    assert gs.validate_route_payload(world, {**base, "routing_alert_ids": [verified.report_id]}).grounded
    r = gs.validate_route_payload(world, {**base, "alert_ids": ["rpt_invented"]})
    assert codes(r) == ["UNKNOWN_ALERT"] and r.violations[0].offending_value == "rpt_invented"
    r = gs.validate_route_payload(world, {**base, "routing_alert_ids": [pending.report_id]})
    assert codes(r) == ["ALERT_NOT_ACTIVE"] and "does not currently affect routing" in r.violations[0].message
    r = gs.validate_route_payload(world, {**base, "alerts": [{"alert_id": verified.report_id, "status": "pending"}]})
    assert codes(r) == ["ALERT_NOT_ACTIVE"]
    svc.admin_reject(world, verified.report_id, user_by_email(world, ADMIN), "wrong")
    assert codes(gs.validate_route_payload(world, {**base, "routing_alert_ids": [verified.report_id]})) == ["ALERT_NOT_ACTIVE"]
    assert gs.validate_route_payload(world, {"route_ids": [RICK], "legs": [leg(RICK, "rickshaw", "FARMGATE", "UTTARA_NORTH",
                                     alert_ids=["nope"])]}).violations[0].field == "legs[0].alert_ids"


# ---- the judge demo -------------------------------------------------------------------------------------------------------
FAKE = {"route_ids": ["BUS_MIRPUR_MOTIJHEEL_DIRECT"], "claimed_fare_bdt": 25, "claimed_duration_min": 20,
        "hub_ids": ["MIRPUR_10", "FARMGATE"]}


def test_fake_direct_bus_fails(world):
    out = gs.fake_grounding_test(world, FAKE)
    assert out["grounded"] is False and out["blocked"] is True and out["safe_for_display"] is False
    got = {v["code"]: v for v in out["violations"]}
    assert got["UNKNOWN_ROUTE_ID"]["offending_value"] == "BUS_MIRPUR_MOTIJHEEL_DIRECT"
    assert got["UNKNOWN_ROUTE_ID"]["message"] == "Route does not exist in transit graph."
    assert got["FARE_MISMATCH"]["offending_value"] == 25
    assert out["fallback_used"] is True and out["fallback_action"]["type"] == "use_route_engine_result"
    assert out["engine_result"]["legs"] == [MRT_M_F] and out["engine_result"]["total_regular_fare_min_bdt"] == 33


def test_fake_test_on_a_grounded_payload_is_not_blocked(world):
    out = gs.fake_grounding_test(world, {"route_ids": [MRT_M_F], "hub_ids": ["MIRPUR_10", "FARMGATE"], "claimed_fare_bdt": 33})
    assert out["grounded"] is True and out["blocked"] is False and out["fallback_action"] is None


def test_empty_payload_is_not_grounded(world):
    r = gs.validate_route_payload(world, {})
    assert not r.grounded and codes(r) == ["NO_PAYLOAD"]


# ---- chat payloads ----------------------------------------------------------------------------------------------------------
def chat(world, text, **payload):
    return {"reply_text": text, "payload": payload}


def test_chat_response_with_grounded_option_and_matching_text_passes(world):
    o = rickshaw_option(world)
    text = "Take the rickshaw: about 100-150 Tk, around 30 minutes."
    r = gs.validate_chat_payload(world, chat(world, text, route_options=[o]))
    assert r.grounded, r.violations
    assert r.checked_route_ids == [RICK]


def test_chat_text_numbers_must_be_backed_by_a_grounded_option(world):
    o = rickshaw_option(world)
    r = gs.validate_chat_payload(world, chat(world, "That costs Tk 20 and takes 5 minutes.", route_options=[o]))
    assert set(codes(r)) == {"FARE_MISMATCH", "DURATION_MISMATCH"} and all(v.field == "reply_text" for v in r.violations)
    r = gs.validate_chat_payload(world, chat(world, "It is only 25 BDT!"))
    assert codes(r) == ["FARE_MISMATCH"]
    assert gs.validate_chat_payload(world, chat(world, "Hello! Which hub are you starting from?")).grounded


def test_chat_option_violations_are_reported_with_their_path(world):
    o = rickshaw_option(world)
    o["legs"][0]["route_id"] = "FAKE_ROUTE"
    r = gs.validate_chat_payload(world, chat(world, "ok", route_options=[o]))
    assert not r.grounded and r.violations[0].field.startswith("payload.routes[0].legs[0]")


def test_chat_entities_and_alerts_are_checked(world):
    r = gs.validate_chat_payload(world, {"reply_text": "hi", "entities": {"origin": "Narnia", "hub_ids": ["FARMGATE", "X9"]},
                                         "payload": {"alert_ids": ["rpt_nope"]}})
    assert sorted(codes(r)) == ["UNKNOWN_ALERT", "UNKNOWN_HUB", "UNKNOWN_HUB"]


# ---- trip search integration ---------------------------------------------------------------------------------------------------
def test_validate_search_result_drops_ungrounded_options(world):
    result = rs.search_routes(world, "Uttara North", "Farmgate")
    n = len(result["routes"])
    clean = gs.validate_search_result(world, result, "normal", None)
    assert clean["badge"] == "grounded" and clean["grounded"] and clean["options_checked"] == n and len(result["routes"]) == n
    result["routes"][0]["legs"][0]["route_id"] = "TAMPERED"
    dirty = gs.validate_search_result(world, result, "normal", None)
    assert dirty["badge"] == "blocked" and dirty["options_dropped"] == 1 and dirty["fallback_used"] is True
    assert len(result["routes"]) == n - 1 and all(l["route_id"] != "TAMPERED" for o in result["routes"] for l in o["legs"])
    assert gs.validate_search_result(world, {"routes": []}, None, None)["options_checked"] == 0


# ---- reliability ------------------------------------------------------------------------------------------------------------------
def test_reliability_payload_has_votes_decay_and_admin_approval(world):
    r = svc.create_report(world, user_by_email(world, USER2), report_payload())
    for who in (USER3, USER1):
        svc.vote_report(world, r.report_id, user_by_email(world, who), 1)
    svc.vote_report(world, r.report_id, make_user(world, "d@x.local"), -1)
    svc.admin_verify(world, r.report_id, user_by_email(world, ADMIN), edge_closed=True)
    p = gs.build_reliability_payload(world, r.report_id)
    assert set(p) == {"report_id", "status", "confidence", "freshness", "upvotes", "downvotes", "community_score",
                      "last_verified_at", "expires_at", "admin_approved", "affects_routing", "needs_admin_review",
                      "contradiction_flag", "reasons"}
    assert (p["status"], p["upvotes"], p["downvotes"], p["community_score"]) == ("verified", 2, 1, 1)
    assert p["admin_approved"] is True and p["affects_routing"] is True and p["freshness"] == pytest.approx(1.0, abs=0.01)
    assert p["confidence"] == pytest.approx(0.45 + 0.10 - 0.08 + 0.20, abs=0.01)
    assert p["last_verified_at"] and p["expires_at"] > p["last_verified_at"]
    assert {"Admin verified", "Multiple recent confirmations", "No contradictory report", "Within expiry window"} <= set(p["reasons"])


def test_reliability_for_pending_flagged_and_closed_reports(world):
    pending = svc.create_report(world, user_by_email(world, USER2), report_payload())
    p = gs.build_reliability_payload(world, pending.report_id)
    assert p["status"] == "pending" and p["admin_approved"] is False and "Awaiting admin verification" in p["reasons"]
    clash = svc.create_report(world, user_by_email(world, USER3), report_payload(affected_hub="FARMGATE", affected_route_ids=[]))
    p = gs.build_reliability_payload(world, clash.report_id)
    assert p["contradiction_flag"] and "Contradictory report flagged" in p["reasons"] and "Flagged for admin review" in p["reasons"]
    svc.admin_reject(world, pending.report_id, user_by_email(world, ADMIN), "no")
    p = gs.build_reliability_payload(world, pending.report_id)
    assert "Report is rejected" in p["reasons"] and "Outside expiry window" in p["reasons"] and p["confidence"] == 0
    from app.responses import ApiError
    with pytest.raises(ApiError) as e:
        gs.build_reliability_payload(world, "rpt_missing")
    assert e.value.code == "REPORT_NOT_FOUND"


# ---- HTTP -----------------------------------------------------------------------------------------------------------------------------
FAKE_BODY = {"route_ids": ["BUS_MIRPUR_MOTIJHEEL_DIRECT"], "claimed_fare_bdt": 25, "claimed_duration_min": 20,
             "hub_ids": ["MIRPUR_10", "FARMGATE"]}  # (MOTIJHEEL is not in the 3-hub test fixture)


def test_fake_test_endpoint_blocks_hallucinated_route(client):
    r = client.post("/api/grounding/fake-test", json=FAKE_BODY, headers=H)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["errors"] == []
    d = body["data"]
    assert d["grounded"] is False and d["blocked"] is True and d["safe_for_display"] is False
    assert {"UNKNOWN_ROUTE_ID", "FARE_MISMATCH"} <= {v["code"] for v in d["violations"]}
    assert d["fallback_action"] == {"type": "use_route_engine_result",
                                    "message": "Discard AI-generated route and use deterministic route engine output."}
    assert r.json()["data"]["violations"][0]["offending_value"] == "BUS_MIRPUR_MOTIJHEEL_DIRECT"


def test_grounding_endpoints_need_the_demo_secret(client):
    for path, body in (("/api/grounding/fake-test", FAKE_BODY), ("/api/grounding/check", {"route_option": {"route_ids": ["x"]}})):
        r = client.post(path, json=body)
        assert r.status_code == 403 and r.json()["errors"][0]["code"] == "DEBUG_SECRET_INVALID"
        assert client.post(path, json=body, headers={"X-Demo-Secret": "nope"}).status_code == 403
        assert client.post(path, json={**body, "secret": settings.demo_secret}).status_code == 200  # body secret works too


def test_check_endpoint_route_option_and_chat_response(client):
    option = client.post("/api/trips/search", json={"origin": "Mirpur 10", "destination": "Farmgate"}).json()["data"]["routes"][0]
    ok = client.post("/api/grounding/check", json={"route_option": option}, headers=H).json()["data"]
    assert ok["grounded"] is True and ok["badge"] == "grounded" and ok["violations"] == []
    option["legs"][0]["route_id"] = "MADE_UP"
    bad = client.post("/api/grounding/check", json={"route_option": option}, headers=H).json()["data"]
    assert bad["grounded"] is False and bad["badge"] == "blocked" and bad["safe_for_display"] is False
    chat = client.post("/api/grounding/check", json={"chat_response": {"reply_text": "It costs 5 Tk"}}, headers=H).json()["data"]
    assert [v["code"] for v in chat["violations"]] == ["FARE_MISMATCH"]


def test_check_endpoint_needs_exactly_one_payload(client):
    for body in ({}, {"route_option": {}, "chat_response": {}}):
        r = client.post("/api/grounding/check", json=body, headers=H)
        assert r.status_code == 422 and r.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_trip_search_returns_a_grounding_badge(client):
    d = client.post("/api/trips/search", json={"origin": "Uttara North", "destination": "Farmgate"}).json()["data"]
    g = d["grounding"]
    assert g["badge"] == "grounded" and g["grounded"] and g["safe_for_display"] and g["violations"] == []
    assert g["options_checked"] == len(d["routes"]) and g["options_dropped"] == 0 and g["fallback_used"] is False
    assert MRT_U_M in g["checked_route_ids"]
    none = client.post("/api/trips/search", json={"origin": "Farmgate", "destination": "Uttara North", "avoid_modes": ["mrt", "local_bus"]}).json()["data"]
    assert none["grounding"]["badge"] == "grounded" and none["routes"] == []


def test_reliability_endpoint(client, engine):
    from app.tests.helpers import headers_for
    with Session(engine) as s:
        user = headers_for(s, USER3)
    client.post("/api/reports/rpt_TEST_FIXTURE_001/vote", json={"value": 1}, headers=user)
    r = client.get("/api/reports/rpt_TEST_FIXTURE_001/reliability")
    d = r.json()["data"]
    assert r.status_code == 200 and d["upvotes"] == 1 and d["status"] == "pending" and d["admin_approved"] is False
    assert client.get("/api/reports/rpt_TEST_FIXTURE_002/reliability").json()["data"]["admin_approved"] is True
    assert client.get("/api/reports/rpt_nope/reliability").status_code == 404
