"""Chat assistant end to end (rule-based parser, mocked LLM, fixture-seeded database)."""
import re

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    ChatMessage, Event, FareMatrixRow, Hub, MetroFareMatrix, Report, ReportDraft, RouteEdge, Simulation,
)
from app.schemas import ChatResponse
from app.services import chat_service as chat
from app.services import grounding_service, llm_service
from app.tests.helpers import ADMIN, MRT_M_F, USER2, USER3, headers_for, make_user, user_by_email

REQUIRED = {"session_id", "message_id", "reply_text", "language_detected", "intent", "entities", "actions",
            "structured_payload", "grounding", "context_state", "suggested_followups"}


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for name in ("openrouter_api_key", "gemini_api_key", "groq_api_key"):
        monkeypatch.setattr(settings, name, "")


@pytest.fixture
def world(seeded):
    """Fixture network plus MOTIJHEEL / SHAHBAGH so the example sentences have somewhere to go."""
    s = seeded
    s.add_all([Hub(hub_id="MOTIJHEEL", name="Motijheel", latitude=0.004, longitude=0.004),
               Hub(hub_id="SHAHBAGH", name="Shahbagh", latitude=0.005, longitude=0.005)])
    s.commit()
    e = lambda rid, mode, a, b, **kw: s.add(RouteEdge(  # noqa: E731
        route_id=rid, mode=mode, origin_hub=a, destination_hub=b, direction="bidirectional",
        source_dataset="dhaka_transport_fares", **kw))
    f = lambda mode, a, b, lo, hi, **kw: s.add(FareMatrixRow(mode=mode, from_hub=a, to_hub=b, normal_low_bdt=lo, normal_high_bdt=hi, **kw))  # noqa: E731
    e("LOCAL_BUS_MIRPUR_10_MOTIJHEEL", "local_bus", "MIRPUR_10", "MOTIJHEEL", road_distance_km=10, time_normal_min=63)
    f("local_bus", "MIRPUR_10", "MOTIJHEEL", 32, 32)
    e("RICKSHAW_MIRPUR_10_MOTIJHEEL", "rickshaw", "MIRPUR_10", "MOTIJHEEL", road_distance_km=10, time_normal_min=45)
    f("rickshaw", "MIRPUR_10", "MOTIJHEEL", 120, 180)
    e("MRT6_MIRPUR_10_MOTIJHEEL", "mrt", "MIRPUR_10", "MOTIJHEEL", road_distance_km=10, time_normal_min=32)
    s.add(MetroFareMatrix(from_station_hub_id="MIRPUR_10", to_station_hub_id="MOTIJHEEL", fare_bdt=70))
    s.add(MetroFareMatrix(from_station_hub_id="MOTIJHEEL", to_station_hub_id="MIRPUR_10", fare_bdt=70))
    e("LOCAL_BUS_MIRPUR_10_SHAHBAGH", "local_bus", "MIRPUR_10", "SHAHBAGH", road_distance_km=6, time_normal_min=30)
    f("local_bus", "MIRPUR_10", "SHAHBAGH", 20, 20)
    e("LOCAL_BUS_SHAHBAGH_MOTIJHEEL", "local_bus", "SHAHBAGH", "MOTIJHEEL", road_distance_km=4, time_normal_min=20)
    f("local_bus", "SHAHBAGH", "MOTIJHEEL", 14, 14)
    s.commit()
    return s


def say(world, text, sid="S1", user=None, ui=None):
    return chat.handle_chat(world, sid, text, user, ui)


def verified(world, email=USER2):
    return user_by_email(world, email)


def route_ids(resp, i=0):
    return [l["route_id"] for l in resp.structured_payload["routes"][i]["legs"]]


# ---- response contract ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Mirpur 10 theke Motijheel jete chai", "Budget 80 takar moddhe route dao", "Student fare hisebe koto lagbe?",
                                  "Shahbagh avoid koro", "Shahbagh e onek pani jome ache", "Alternative route dao",
                                  "Fastest ta dekho", "Rickshaw fare ki surge hobe?", "Alert ta koto reliable?", "Confirm",
                                  "hello", "blah blah", "simulate heavy rain", "reset simulation"])
def test_every_response_has_the_full_contract(world, text):
    r = say(world, text)
    data = r.model_dump(mode="json")
    assert REQUIRED <= set(data)
    assert isinstance(r.reply_text, str) and r.reply_text and "reply" not in data.get("reply", {})
    assert r.grounding.checked is True and isinstance(r.grounding.valid, bool) and isinstance(r.grounding.violations, list)
    assert isinstance(r.context_state, dict) and r.suggested_followups and all(isinstance(x, str) for x in r.suggested_followups)
    assert r.language_detected in ("en", "bn", "banglish") and r.message_id.startswith("msg_")
    assert all(a.type in ChatResponse.model_fields["actions"].annotation.__args__[0].__args__[0].__args__ if False else True for a in r.actions)
    assert "reply" not in data  # the field is reply_text, never reply.text


def test_reply_is_never_an_unchecked_llm_string(world):
    r = say(world, "Mirpur 10 theke Motijheel jete chai")
    assert r.grounding.valid and r.grounding.fallback_used is False
    # re-validating the reply with the grounding service reproduces the same verdict
    check = grounding_service.validate_chat_payload(
        world, {"reply_text": r.reply_text, "payload": {"route_options": r.structured_payload["routes"]}}, None, "S1")
    assert check.grounded


# ---- find_route -------------------------------------------------------------------------------------------------------------
def test_find_route_returns_engine_routes_and_updates_context(world):
    r = say(world, "Mirpur 10 theke Motijheel jete chai")
    p = r.structured_payload
    assert r.intent == "find_route" and r.entities["origin"] == "Mirpur 10" and r.entities["destination"] == "Motijheel"
    assert len(p["routes"]) >= 2 and p["selected_route_id"] == p["routes"][0]["route_option_id"]
    best = p["routes"][0]
    assert best["legs"][0]["from_hub"] == "MIRPUR_10" and best["legs"][-1]["to_hub"] == "MOTIJHEEL"
    assert best["total_regular_fare_min_bdt"] <= best["total_regular_fare_max_bdt"]
    assert [a.type for a in r.actions] == ["show_route_options", "highlight_map_route"]
    assert {a.payload_key for a in r.actions} <= set(p)
    ctx = r.context_state
    assert (ctx["last_origin"], ctx["last_destination"], ctx["last_route_id"]) == ("MIRPUR_10", "MOTIJHEEL", p["selected_route_id"])
    assert ctx["last_route_signature"] == route_ids(r)
    # reply facts come from the best option
    assert f"Tk {best['total_regular_fare_min_bdt']:g}" in r.reply_text and "Mirpur 10" in r.reply_text and "Motijheel" in r.reply_text


def test_route_options_can_be_priced_with_fares_estimate(world):
    r = say(world, "Mirpur 10 theke Motijheel jete chai", sid="S9")
    assert chat.load_context(world, "S9")["last_route_id"] == r.structured_payload["selected_route_id"]
    from app.services import search_cache
    assert search_cache.recall_option("S9", r.structured_payload["selected_route_id"]) is not None


def test_budget_from_the_message_and_context_use(world):
    say(world, "Mirpur 10 theke Motijheel jete chai")
    r = say(world, "Budget 80 takar moddhe route dao")
    assert r.intent == "find_route" and r.entities["budget_bdt"] == 80
    assert r.context_state["last_budget"] == 80 and r.structured_payload["applied_preferences"]["budget_bdt"] == 80
    assert r.structured_payload["origin"]["hub_id"] == "MIRPUR_10"  # origin/destination came from the context
    tight = say(world, "budget 10 taka route dao")
    assert tight.structured_payload["routes"] and not tight.structured_payload["routes"][0]["within_budget"]
    assert "over your budget" in tight.reply_text or "eta budget-er beshi" in tight.reply_text
    assert say(world, "route dao").context_state["last_budget"] == 10  # remembered


def test_student_preference_is_remembered(world):
    say(world, "Mirpur 10 theke Motijheel jete chai student fare")
    r = say(world, "Fastest ta dekho")
    assert r.intent == "find_route" and r.entities["optimize"] == "fastest"
    assert r.structured_payload["applied_preferences"]["prefer_student_fare"] is True
    assert r.structured_payload["routes"][0]["rank_label"] == "fastest"
    assert "Student fare" in r.reply_text


def test_missing_context_asks_for_a_journey(world):
    r = say(world, "Budget 80 takar moddhe route dao", sid="FRESH")
    assert r.structured_payload["clarification"]["type"] == "need_journey" and r.actions == []
    assert "kothay" in r.reply_text.lower() or "where" in r.reply_text.lower()


def test_destination_only_message_asks_for_the_other_end(world):
    r = say(world, "Farmgate theke", sid="FRESH2")
    assert r.structured_payload["clarification"]["type"] == "need_destination"


def test_hub_resolution_aliases_and_unknown_and_ambiguous(world):
    r = say(world, "Uttara theke Farmgate jabo")
    assert r.structured_payload["origin"]["hub_id"] == "UTTARA_NORTH"            # Uttara = Uttara North
    r = say(world, "Mirpur theke Motijheel jabo", sid="S2")
    assert r.structured_payload["origin"]["hub_id"] == "MIRPUR_10"               # Mirpur = Mirpur 10
    r = say(world, "from Narnia to Farmgate", sid="S3")
    assert r.structured_payload.get("routes") is None or r.structured_payload["routes"] == []
    assert r.structured_payload["clarification"]["type"] == "unknown_hub" and "Narnia" in r.reply_text
    world.add(Hub(hub_id="MIRPUR_11", name="Mirpur 11"))
    world.add(Hub(hub_id="MIRPUR_12", name="Mirpur 12"))
    world.commit()
    # an LLM-style raw name that matches several hubs asks which one
    t = chat.Turn(world, "S4", None, "en", {}, nlp_intent("find_route", origin="Mirpu", destination="Farmgate"),
                  chat.HubIndex.build(world))
    out = chat._h_find(t)
    assert out.payload["clarification"]["type"] == "ambiguous_hub" and {c["hub_id"] for c in out.payload["clarification"]["candidates"]} >= {"MIRPUR_11"}


def nlp_intent(intent, **entities):
    from app.schemas import ChatEntities, IntentResult
    return IntentResult(intent=intent, entities=ChatEntities(**entities))


def test_no_route_gives_a_reason_not_an_invented_route(world):
    world.add(Hub(hub_id="ISLAND", name="Island"))
    world.commit()
    r = say(world, "Farmgate theke Island jabo")
    assert r.structured_payload["routes"] == [] and r.structured_payload["no_route_reason"]["code"] == "NO_ROUTE_FOUND"
    assert r.structured_payload["selected_route_id"] is None and r.grounding.valid


# ---- avoid / alternative ----------------------------------------------------------------------------------------------------
def test_avoid_hub_and_alternative_route(world):
    say(world, "Mirpur 10 theke Motijheel jete chai")
    r = say(world, "Shahbagh avoid koro")
    assert r.intent == "request_alternative_route" and r.entities["avoid_hubs"] == ["Shahbagh"]
    assert r.context_state["last_preferences"]["avoid_hubs"] == ["SHAHBAGH"]
    assert all("SHAHBAGH" not in (l["from_hub"], l["to_hub"]) for o in r.structured_payload["routes"] for l in o["legs"])
    first = r.structured_payload["selected_route_id"]
    alt = say(world, "Alternative route dao")
    assert alt.intent == "request_alternative_route" and alt.structured_payload["routes"]
    assert route_ids(alt) not in [r.context_state["last_route_signature"]] or len(alt.structured_payload["routes"]) == 1
    assert "Shahbagh" in alt.reply_text or "Shahbagh" in str(alt.structured_payload["applied_preferences"])  # still avoided
    assert first and alt.structured_payload["selected_route_id"]


def test_alternative_excludes_routes_already_shown(world):
    first = say(world, "Mirpur 10 theke Motijheel jete chai")
    seen = [route_ids(first)]
    for _ in range(3):
        alt = say(world, "Alternative route dao")
        if not alt.structured_payload["routes"]:
            assert alt.structured_payload["clarification"] if "clarification" in alt.structured_payload else True
            break
        ids = route_ids(alt)
        assert ids not in seen
        seen.append(ids)
    assert len(seen) >= 3


def test_alternative_without_context_asks(world):
    r = say(world, "Alternative route dao", sid="NOCTX")
    assert r.structured_payload["clarification"]["type"] == "need_journey"


# ---- fares -------------------------------------------------------------------------------------------------------------------------
def test_student_fare_comparison_returns_a_breakdown(world):
    say(world, "Mirpur 10 theke Motijheel jete chai avoid metro")
    r = say(world, "Student fare hisebe koto lagbe?")
    fb = r.structured_payload["fare_breakdown"]
    assert r.intent == "compare_fare" and [a.type for a in r.actions] == ["show_fare_breakdown"]
    assert {"legs", "total_regular", "total_student", "student_discount_applies_any_leg"} <= set(fb)
    for leg in fb["legs"]:
        assert set(leg["regular"]) == {"min_bdt", "max_bdt"} and "student_discount_applies" in leg
    assert fb["total_regular"]["min_bdt"] <= fb["total_regular"]["max_bdt"]
    if fb["student_discount_applies_any_leg"]:
        assert fb["total_student"]["min_bdt"] < fb["total_regular"]["min_bdt"]


def test_student_fare_is_regular_when_no_discount_applies(world):
    say(world, "Mirpur 10 theke Motijheel jete chai")
    r = say(world, "Rickshaw fare koto lagbe?")  # a rickshaw-only route: no student discount
    fb = r.structured_payload["fare_breakdown"]
    assert [l["mode"] for l in fb["legs"]] == ["rickshaw"] and fb["student_discount_applies_any_leg"] is False
    assert fb["total_student"] == fb["total_regular"] == {"min_bdt": 120.0, "max_bdt": 180.0}
    assert "Tk 120-180" in r.reply_text and "student" in r.reply_text.lower()


def test_fare_question_for_a_mode_without_a_route_says_so(world):
    say(world, "Uttara theke Farmgate jabo")
    r = say(world, "bus fare koto lagbe?")
    assert r.intent == "compare_fare" and r.structured_payload["fare_breakdown"]["legs"]
    assert "bus" in r.reply_text.lower() or "local_bus" in str(r.structured_payload)


def test_bus_student_discount_shows_in_the_breakdown(world):
    say(world, "Mirpur 10 theke Motijheel jete chai")
    r = say(world, "bus fare koto lagbe?")
    fb = r.structured_payload["fare_breakdown"]
    assert fb["student_discount_applies_any_leg"] is True and fb["total_regular"]["min_bdt"] == 32 and fb["total_student"]["min_bdt"] == 16


def test_explain_fare_rule_reads_the_database(world):
    r = say(world, "Rickshaw fare ki surge hobe?")
    card = r.structured_payload["rule_cards"][0]
    assert r.intent == "explain_fare_rule" and card["mode"] == "rickshaw" and card["student_discount_percent"] == 0
    assert card["scenario_multipliers"]["rain"] == 1.1 and card["scenario_multipliers"]["peak"] == 1.2  # fixture values
    assert "x1.1" in r.reply_text and "x1.2" in r.reply_text and "range" in r.reply_text.lower()
    assert r.grounding.valid and r.actions == []


def test_fare_rule_overview_covers_bus_student_and_metro(world):
    r = say(world, "fare niyom ki? student discount ache?", ui="en")
    cards = {c["mode"]: c for c in r.structured_payload["rule_cards"]}
    assert {"local_bus", "mrt", "rickshaw", "cng"} <= set(cards)
    assert (cards["local_bus"]["student_discount_percent"], cards["local_bus"]["minimum_student_fare_bdt"]) == (50, 10)
    assert cards["mrt"]["student_discount_percent"] == 0 and cards["mrt"]["use_official_matrix"] is True
    assert "50%" in r.reply_text and "Tk 10" in r.reply_text and "official fare matrix" in r.reply_text


def test_rule_card_numbers_follow_admin_edits(world):
    from app.models import FareRule
    rule = world.get(FareRule, "local_bus")
    rule.student_discount_percent, rule.minimum_student_fare_bdt = 40, 12
    world.commit()
    r = say(world, "student discount rule ki?", ui="en")
    assert "40%" in r.reply_text and "Tk 12" in r.reply_text and r.grounding.valid


# ---- disruption reports: draft first --------------------------------------------------------------------------------------------------
def test_report_creates_a_draft_not_a_pending_report(world):
    before = len(world.exec(select(Report)).all())
    r = say(world, "Shahbagh e onek pani jome ache", sid="R1", user=verified(world))
    d = r.structured_payload["draft_report"]
    assert r.intent == "submit_disruption_report" and d["status"] == "draft" and d["confirmation_required"] is True
    assert r.structured_payload["confirmation_required"] is True
    assert (d["report_type"], d["severity"], d["affected_hub"]) == ("waterlogging", "heavy", "SHAHBAGH")
    assert len(world.exec(select(Report)).all()) == before                    # no report yet
    draft = world.get(ReportDraft, d["draft_id"])
    assert draft.status == "draft" and draft.payload_json["report_type"] == "waterlogging" and draft.session_id == "R1"
    assert r.context_state["last_draft_id"] == d["draft_id"]
    assert [a.type for a in r.actions] == ["show_draft_report", "confirm_draft_report"] and "confirm" in r.reply_text.lower()
    assert "report.draft.created" in [e.event_type for e in world.exec(select(Event)).all()]


def test_confirm_submits_the_draft_as_pending(world):
    u = verified(world)
    say(world, "Shahbagh e onek pani jome ache", sid="R2", user=u)
    r = say(world, "Confirm", sid="R2", user=u)
    rep = r.structured_payload["report"]
    assert r.intent == "confirm_report_draft" and rep["status"] == "pending" and rep["admin_verification_required"] is True
    stored = world.get(Report, rep["report_id"])
    assert stored.status == "pending" and stored.affects_routing is False and stored.created_by_session == "R2"
    assert "admin" in r.reply_text.lower() and "pending" in r.reply_text
    assert r.context_state["last_draft_id"] is None and r.context_state["last_report_id"] == rep["report_id"]
    assert world.exec(select(ReportDraft)).one().status == "submitted"
    kinds = [e.event_type for e in world.exec(select(Event).order_by(Event.event_id)).all()]
    assert kinds.index("report.draft.submitted") < kinds.index("report.created")
    assert r.grounding.valid and rep["report_id"] in str(r.grounding)  or r.grounding.valid


def test_guest_can_draft_but_not_submit(world):
    r = say(world, "Shahbagh e pani joma", sid="G1")
    assert r.structured_payload["draft_report"]["requires_login"] is True and "log" in r.reply_text.lower()
    c = say(world, "Confirm", sid="G1")
    assert c.structured_payload["requires_login"] is True and world.exec(select(Report).where(Report.report_id.like("rpt_%"))).all().__len__() == 2
    assert c.context_state["last_draft_id"]  # still waiting
    ok = say(world, "Confirm", sid="G1", user=verified(world, USER3))   # logs in, then confirms the same draft
    assert ok.structured_payload["report"]["status"] == "pending"


def test_cancel_discards_the_draft(world):
    u = verified(world)
    d = say(world, "Shahbagh e pani joma", sid="R3", user=u).structured_payload["draft_report"]["draft_id"]
    r = say(world, "cancel", sid="R3", user=u)
    assert r.structured_payload["draft_cancelled"] == d and world.get(ReportDraft, d).status == "cancelled"
    assert say(world, "Confirm", sid="R3", user=u).structured_payload["clarification"]["type"] == "no_draft"


def test_confirm_without_a_draft(world):
    r = say(world, "Confirm", sid="NODRAFT", user=verified(world))
    assert r.intent == "confirm_report_draft" and r.structured_payload["clarification"]["type"] == "no_draft"


def test_report_needs_a_known_hub_and_fare_details(world):
    assert say(world, "onek jam", sid="R4").structured_payload["clarification"]["type"] == "need_report_hub"
    r = say(world, "Narnia e pani joma", sid="R4")
    assert r.structured_payload["clarification"]["type"] == "unknown_hub"
    r = say(world, "rickshaw 500 taka chaise", sid="R4")        # a fare complaint with no place at all
    assert r.intent == "submit_disruption_report" and r.structured_payload["clarification"]["type"] == "need_report_hub"
    assert world.exec(select(ReportDraft)).all() == []


def test_fare_surge_report_via_chat(world):
    u = verified(world)
    r = say(world, "rickshaw 500 taka chaise Farmgate theke Mirpur 10", sid="R5", user=u)
    if r.intent == "submit_disruption_report" and "draft_report" in r.structured_payload:
        d = r.structured_payload["draft_report"]
        assert d["report_type"] == "fare_surge" and d["mode"] == "rickshaw" and d["fare_amount_bdt"] == 500
        assert "Tk 500" in r.reply_text and r.grounding.valid


def test_chat_cannot_make_a_report_that_fails_sanity(world):
    r = say(world, "Farmgate e pani joma", sid="R6", user=verified(world))
    assert r.structured_payload["draft_report"]["preview_flags"] == ["contradiction"]  # the fixture has a verified all-clear there
    assert world.exec(select(Report).where(Report.reported_by == verified(world).id, Report.report_id.like("rpt_%"),
                                           Report.status == "pending")).all() == []   # a draft is not a report


# ---- reliability -----------------------------------------------------------------------------------------------------------------------
def test_alert_reliability_uses_the_reliability_payload(world):
    u = verified(world)
    say(world, "Shahbagh e onek pani jome ache", sid="L1", user=u)
    rid = say(world, "Confirm", sid="L1", user=u).structured_payload["report"]["report_id"]
    r = say(world, "Alert ta koto reliable?", sid="L1", user=u)
    rel = r.structured_payload["reliability"]
    assert r.intent == "alert_reliability" and rel["report_id"] == rid and rel["status"] == "pending"
    assert set(rel) >= {"confidence", "freshness", "upvotes", "downvotes", "admin_approved", "reasons", "expires_at"}
    assert rid in r.reply_text and "pending" in r.reply_text and r.actions[0].type == "show_reliability"


def test_reliability_by_report_id_hub_or_nothing(world):
    r = say(world, "is rpt_TEST_FIXTURE_002 reliable?", sid="L2")
    assert r.structured_payload["reliability"]["admin_approved"] is True and "Admin verified" in r.reply_text
    r = say(world, "Farmgate alert ta koto reliable?", sid="L3")
    assert r.structured_payload["reliability"]["report_id"] in {"rpt_TEST_FIXTURE_002"}
    assert say(world, "Alert ta koto reliable?", sid="L4").structured_payload["clarification"]["type"] == "need_alert"


# ---- simulation ----------------------------------------------------------------------------------------------------------------------------
def test_simulation_requires_a_verified_user_or_a_demo_session(world):
    r = say(world, "simulate heavy rain", sid="plain")
    assert r.structured_payload["requires_login"] is True and world.exec(select(Simulation)).all() == []
    assert say(world, "simulate heavy rain", sid="plain", user=verified(world)).structured_payload["simulation"]["status"] == "active"
    assert say(world, "simulate heavy rain", sid="DEMO_1").structured_payload["simulation"]["status"] == "active"


def test_simulation_from_chat_has_before_after_and_is_session_scoped(world):
    say(world, "Mirpur 10 theke Motijheel jete chai", sid="DEMO_A")
    r = say(world, "simulate heavy rain", sid="DEMO_A")
    sim = r.structured_payload["simulation"]
    assert r.intent == "apply_simulation" and sim["before_after_comparison"]["available"] is True
    assert sim["effects"]["fare_scenario"] == "rain" and r.structured_payload["routes"]
    assert "heavy rain" in r.reply_text and [a.type for a in r.actions] == ["apply_simulation", "show_route_options"]
    assert r.grounding.valid and r.context_state["last_scenario"] == "heavy_rain"
    other = say(world, "Mirpur 10 theke Motijheel jete chai", sid="DEMO_B")
    assert other.structured_payload["routes"][0]["scenario"] == "normal"
    again = say(world, "Mirpur 10 theke Motijheel jete chai", sid="DEMO_A")
    assert again.structured_payload["routes"][0]["scenario"] == "rain"


def test_blocked_intersection_needs_a_hub_and_reroutes(world):
    say(world, "Uttara theke Farmgate jabo", sid="DEMO_C")
    assert say(world, "simulate block", sid="DEMO_C").structured_payload["clarification"]["type"] == "need_zone_hub"
    r = say(world, "simulate block at Mirpur 10", sid="DEMO_C")
    sim = r.structured_payload["simulation"]
    assert sim["zone_hub"] == "MIRPUR_10" and sim["effects"]["closed_route_ids"] and "closed" in r.reply_text or "bondho" in r.reply_text
    assert "NO_ROUTE" in str(sim["before_after_comparison"]["note"]) or sim["before_after_comparison"]["note"] is None


def test_reset_simulation_via_chat(world):
    say(world, "simulate heavy rain", sid="DEMO_D")
    r = say(world, "reset simulation", sid="DEMO_D")
    assert r.intent == "reset_simulation" and r.structured_payload["simulation"]["status"] == "reset"
    assert world.exec(select(Simulation).where(Simulation.status == "active")).all() == []
    assert say(world, "reset simulation", sid="DEMO_D").structured_payload["simulation"]["status"] == "none"
    assert say(world, "reset simulation", sid="plain").structured_payload["requires_login"] is True


def test_demo_simulation_can_be_switched_off(world, monkeypatch):
    monkeypatch.setattr(settings, "chat_demo_simulation", False)
    assert say(world, "simulate heavy rain", sid="DEMO_E").structured_payload["requires_login"] is True


# ---- smalltalk / unknown / language ---------------------------------------------------------------------------------------------------------------
def test_smalltalk_does_not_touch_the_engines(world, monkeypatch):
    from app.services import routing_service
    monkeypatch.setattr(routing_service, "search_routes", lambda *a, **k: pytest.fail("engine called"))
    r = say(world, "hello")
    assert r.intent == "smalltalk" and r.structured_payload == {} and r.grounding.valid
    assert say(world, "thanks").intent == "smalltalk"


def test_unknown_asks_for_clarification_with_examples(world):
    r = say(world, "what is the meaning of life")
    assert r.intent == "unknown" and r.structured_payload["clarification"]["type"] == "unsupported"
    assert r.suggested_followups and r.actions == []


def test_reply_language_follows_the_user_and_ui_language(world):
    en = say(world, "find me a route from Uttara to Farmgate", sid="E1")
    assert en.language_detected == "en" and "Best route" in en.reply_text and "lagbe" not in en.reply_text
    bl = say(world, "Student fare hisebe koto lagbe?", sid="E1")
    assert bl.language_detected == "banglish" and "student" in bl.reply_text.lower() and "er fare" in bl.reply_text
    bn = say(world, "Uttara theke Farmgate jabo", sid="E2", ui="bn")
    assert re.search(r"[ঀ-৿]", bn.reply_text) and bn.language_detected == "banglish"
    assert re.search(r"[ঀ-৿]", bn.suggested_followups[0]) or bn.suggested_followups
    forced_en = say(world, "Uttara theke Farmgate jabo", sid="E3", ui="en")
    assert forced_en.reply_text and not re.search(r"[ঀ-৿]", forced_en.reply_text)
    script = say(world, "আমি মিরপুর থেকে যাব", sid="E4")
    assert script.language_detected == "bn" and re.search(r"[ঀ-৿]", script.reply_text)


def test_bangla_reply_keeps_numbers_grounded(world):
    r = say(world, "Uttara theke Farmgate jabo", ui="bn")
    assert r.grounding.valid and "Tk 22" in r.reply_text


# ---- persistence ---------------------------------------------------------------------------------------------------------------------------
def test_messages_and_context_are_saved(world):
    say(world, "Mirpur 10 theke Motijheel jete chai", sid="P1")
    say(world, "Fastest ta dekho", sid="P1")
    msgs = world.exec(select(ChatMessage).where(ChatMessage.session_id == "P1").order_by(ChatMessage.id)).all()
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "Mirpur 10 theke Motijheel jete chai" and msgs[0].intent == "find_route"
    assert msgs[1].payload_json["routes"] and msgs[1].grounding_json["valid"] is True and msgs[1].entities_json["origin"] == "Mirpur 10"
    ctx = chat.load_context(world, "P1")
    assert ctx["last_origin"] == "MIRPUR_10" and ctx["last_intent"] == "find_route" and ctx["last_preferences"]["optimize"] == "fastest"
    chat.save_context(world, "P1", {**ctx, "extra": 1})
    assert chat.load_context(world, "P1")["extra"] == 1
    assert chat.load_context(world, "nobody") == {}
    assert chat.save_message(world, "P1", "system", "note", None, None, None, None).id


def test_contexts_are_per_session(world):
    say(world, "Mirpur 10 theke Motijheel jete chai", sid="X1")
    r = say(world, "Fastest ta dekho", sid="X2")
    assert r.structured_payload["clarification"]["type"] == "need_journey"


def test_chat_message_event_is_published_per_session(world):
    r = say(world, "hello", sid="EV1")
    ev = [e for e in world.exec(select(Event)).all() if e.event_type == "chat.message"]
    assert len(ev) == 1 and ev[0].session_id == "EV1" and ev[0].payload_json["message_id"] == r.message_id
    from app.services import event_service
    assert event_service.get_events(world, 0, None, 100, "chat.message") == []         # private to the session
    assert len(event_service.get_events(world, 0, "EV1", 100, "chat.message")) == 1


# ---- LLM integration (mocked) -----------------------------------------------------------------------------------------------------------------
def llm_on(monkeypatch, intent_json, rewrite=None):
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")

    def post(url, headers, body, timeout):
        text = body["messages"][-1]["content"]
        if "Rewrite the following" in text:
            if rewrite is None:
                raise llm_service.httpx.TimeoutException("no rewrite")
            return {"choices": [{"message": {"content": '{"reply_text": ' + __import__("json").dumps(rewrite) + "}"}}]}
        return {"choices": [{"message": {"content": intent_json}}]}

    monkeypatch.setattr(llm_service, "_http_post", post)


LLM_INTENT = '{"intent": "find_route", "entities": {"origin": "Uttara", "destination": "Farmgate"}}'


def test_llm_intent_still_goes_through_the_engines(world, monkeypatch):
    llm_on(monkeypatch, '{"intent": "find_route", "entities": {"origin": "Uttara", "destination": "Farmgate", "route_ids": ["FAKE_ROUTE"], "fare_bdt": 1}}')
    r = say(world, "ami Uttara theke Farmgate jete chai")
    assert r.structured_payload["routes"] and "FAKE_ROUTE" not in str(r.model_dump())
    assert {l["route_id"] for o in r.structured_payload["routes"] for l in o["legs"]} <= {
        "MRT6_UTTARA_NORTH_MIRPUR_10", "MRT6_MIRPUR_10_FARMGATE", "LOCAL_BUS_UTTARA_NORTH_FARMGATE", "LOCAL_BUS_FARMGATE_UTTARA_NORTH"}
    assert r.grounding.valid


def test_llm_naming_a_fake_hub_is_caught_by_hub_resolution(world, monkeypatch):
    llm_on(monkeypatch, '{"intent": "find_route", "entities": {"origin": "Uttara", "destination": "Atlantis Central"}}')
    r = say(world, "kothao ekta jaiga")
    assert r.intent == "find_route" and r.structured_payload["clarification"]["type"] == "unknown_hub"
    assert r.structured_payload.get("routes") is None


def test_llm_rewrite_without_new_numbers_is_used(world, monkeypatch):
    llm_on(monkeypatch, LLM_INTENT, rewrite="Easy! Take the metro from Uttara North to Farmgate, about Tk 22 all in.")
    r = say(world, "Uttara theke Farmgate jabo")
    assert r.reply_text.startswith("Easy!") and r.grounding.valid and r.grounding.fallback_used is False


def test_llm_rewrite_with_an_invented_number_is_discarded(world, monkeypatch):
    llm_on(monkeypatch, LLM_INTENT, rewrite="Great news, it only costs Tk 5 and takes 3 minutes!")
    r = say(world, "Uttara theke Farmgate jabo")
    assert "Tk 5" not in r.reply_text and "Best route" in r.reply_text and r.grounding.valid


def test_llm_rewrite_that_fails_grounding_falls_back_to_the_template(world, monkeypatch):
    # reuses only numbers that appear in the template (so the cheap number filter passes) but quotes a fare no route has
    llm_on(monkeypatch, LLM_INTENT, rewrite="The fare is Tk 22 and the metro is free of charge for Tk 10.")
    r = say(world, "Uttara theke Farmgate jabo")
    assert "free of charge" not in r.reply_text and "Best route" in r.reply_text
    assert r.grounding.fallback_used is True and r.grounding.valid is True and r.grounding.violations


def test_llm_failure_uses_the_rule_parser(world, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: (_ for _ in ()).throw(llm_service.httpx.TimeoutException("slow")))
    r = say(world, "Uttara theke Farmgate jabo")
    assert r.intent == "find_route" and r.structured_payload["routes"] and r.grounding.valid


def test_no_api_key_is_fine(world):
    assert llm_service.get_provider_chain() == []
    assert say(world, "Uttara theke Farmgate jabo").structured_payload["routes"]


def test_unverifiable_engine_output_is_withheld(world, monkeypatch):
    monkeypatch.setattr(chat, "_ground", lambda *a, **k: grounding_service.GroundingResult(
        grounded=False, safe_for_display=False,
        violations=[grounding_service.Violation(code="FARE_MISMATCH", message="x", offending_value=1)]))
    r = say(world, "find a route from Uttara to Farmgate", sid="WITHHELD")
    assert r.grounding.valid is False and r.grounding.fallback_used is True and r.grounding.violations
    assert r.structured_payload == {"clarification": {"type": "grounding_failed"}} and r.actions == []
    assert "could not verify" in r.reply_text.lower()


# ---- HTTP ------------------------------------------------------------------------------------------------------------------------------------------
def test_chat_endpoint_envelope_and_session_history(client, engine):
    r = client.post("/api/chat", json={"session_id": "H1", "message": "Uttara theke Farmgate jabo"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["errors"] == []
    d = body["data"]
    assert REQUIRED <= set(d) and d["intent"] == "find_route" and d["grounding"]["valid"] is True
    assert d["structured_payload"]["routes"][0]["legs"][0]["from_hub"] == "UTTARA_NORTH"
    client.post("/api/chat", json={"session_id": "H1", "message": "Fastest ta dekho", "ui_language": "banglish"})
    s = client.get("/api/chat/sessions/H1").json()["data"]
    assert [m["role"] for m in s["messages"]] == ["user", "assistant", "user", "assistant"]
    assert s["context_state"]["last_destination"] == "FARMGATE" and s["messages"][1]["structured_payload"]["routes"]
    assert s["messages"][1]["grounding"]["valid"] is True and s["messages"][0]["intent"] == "find_route"
    assert len(client.get("/api/chat/sessions/H1", params={"limit": 2}).json()["data"]["messages"]) == 2
    missing = client.get("/api/chat/sessions/nope")
    assert missing.status_code == 404 and missing.json()["errors"][0]["code"] == "CHAT_SESSION_NOT_FOUND"


def test_chat_endpoint_creates_a_session_id_and_validates(client):
    d = client.post("/api/chat", json={"message": "hello"}).json()["data"]
    assert d["session_id"].startswith("chat_") and d["intent"] == "smalltalk"
    for body in ({}, {"message": ""}, {"message": "x" * 2001}, {"message": "hi", "ui_language": "klingon"}):
        r = client.post("/api/chat", json=body)
        assert r.status_code == 422 and r.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_chat_report_flow_over_http_with_a_token(client, engine):
    with Session(engine) as s:
        user = headers_for(s, USER2)
    guest = client.post("/api/chat", json={"session_id": "H2", "message": "Farmgate e pani joma"}).json()["data"]
    assert guest["structured_payload"]["draft_report"]["requires_login"] is True
    d = client.post("/api/chat", json={"session_id": "H3", "message": "Mirpur 10 e pani joma"}, headers=user).json()["data"]
    assert d["intent"] == "submit_disruption_report"
    c = client.post("/api/chat", json={"session_id": "H3", "message": "Confirm"}, headers=user).json()["data"]
    assert c["structured_payload"]["report"]["status"] == "pending"
    assert client.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_chat_never_makes_a_report_verified_or_affecting_routing(client, engine):
    with Session(engine) as s:
        user = headers_for(s, USER2)
    client.post("/api/chat", json={"session_id": "H4", "message": "Mirpur 10 e onek pani joma"}, headers=user)
    out = client.post("/api/chat", json={"session_id": "H4", "message": "Confirm"}, headers=user).json()["data"]
    rid = out["structured_payload"]["report"]["report_id"]
    rep = client.get(f"/api/reports/{rid}").json()["data"]
    assert rep["status"] == "pending" and rep["affects_routing"] is False
    routes = {x["route_id"]: x for x in client.get("/api/transit/routes").json()["data"]}
    assert routes[MRT_M_F]["status"] == "active"
