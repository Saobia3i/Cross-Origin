"""POST /api/trips/search and POST /api/fares/estimate on the fixture-seeded database.

Fixture network: UTTARA_NORTH -> MIRPUR_10 -> FARMGATE by metro (forward only, no times, matrix fares:
U-M 11, M-F 33, U-F 22), plus a dataset-closed one-way bus FARMGATE -> UTTARA_NORTH. Tests add extra edges
where they need alternatives.
"""
import sys
import types

import pytest
from sqlmodel import Session

from app.models import FareMatrixRow, Hub, RouteEdge, Simulation

MRT_U_M = "MRT6_UTTARA_NORTH_MIRPUR_10"
MRT_M_F = "MRT6_MIRPUR_10_FARMGATE"


def add(engine, *objs):
    with Session(engine) as s:
        for o in objs:
            s.add(o)
        s.commit()


def add_rickshaw(engine, lo=100, hi=150):
    """FARMGATE<->UTTARA_NORTH rickshaw (fare dataset => symmetric), with a fare range and a time."""
    add(engine,
        RouteEdge(route_id="RICKSHAW_FARMGATE_UTTARA_NORTH", mode="rickshaw", origin_hub="FARMGATE",
                  destination_hub="UTTARA_NORTH", direction="unknown", source_dataset="dhaka_transport_fares",
                  time_normal_min=30, road_distance_km=5.0),
        FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=lo, normal_high_bdt=hi))


def add_open_bus(engine):
    add(engine,
        RouteEdge(route_id="LOCAL_BUS_UTTARA_NORTH_FARMGATE", mode="local_bus", origin_hub="UTTARA_NORTH",
                  destination_hub="FARMGATE", direction="bidirectional", time_normal_min=70, road_distance_km=9.0),
        FareMatrixRow(mode="local_bus", from_hub="UTTARA_NORTH", to_hub="FARMGATE", normal_low_bdt=30, normal_high_bdt=30))


def search(client, **body):
    body.setdefault("origin", "Uttara North")
    body.setdefault("destination", "Farmgate")
    return client.post("/api/trips/search", json=body)


def data(r):
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["errors"] == []
    return body["data"]


def error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["ok"] is False and body["data"] is None
    assert body["errors"][0]["code"] == code
    return body["errors"][0]


# ---- success shape -------------------------------------------------------------------------------
def test_search_returns_envelope_routes_and_fare_ranges(client):
    d = data(search(client, session_id="DEMO_SESSION"))
    assert set(d) >= {"query_echo", "routes", "no_route_reason", "session_id", "active_scenario"}
    assert d["session_id"] == "DEMO_SESSION" and d["active_scenario"] is None and d["no_route_reason"] is None
    assert d["query_echo"]["origin"] == "Uttara North" and d["query_echo"]["parsed_from_text"] is False
    assert d["routes"]
    for o in d["routes"]:
        for k in ("total_regular_fare_min_bdt", "total_regular_fare_max_bdt", "total_student_fare_min_bdt",
                  "total_student_fare_max_bdt"):
            assert isinstance(o[k], (int, float))
        assert o["total_regular_fare_min_bdt"] <= o["total_regular_fare_max_bdt"]
        for leg in o["legs"]:
            assert "student_discount_applies" in leg and "regular_fare_min_bdt" in leg and "regular_fare_max_bdt" in leg
    metro = d["routes"][0]
    assert [l["route_id"] for l in metro["legs"]] == [MRT_U_M, MRT_M_F]
    assert (metro["total_regular_fare_min_bdt"], metro["total_regular_fare_max_bdt"]) == (22, 22)  # one ride, official matrix


def test_rickshaw_route_is_a_real_range(client, engine):
    add_rickshaw(engine)
    d = data(search(client, optimize="fastest"))
    rick = next(o for o in d["routes"] if o["legs"][0]["mode"] == "rickshaw")
    assert (rick["total_regular_fare_min_bdt"], rick["total_regular_fare_max_bdt"]) == (100, 150)
    assert rick["legs"][0]["regular_fare_min_bdt"] < rick["legs"][0]["regular_fare_max_bdt"]
    assert any("estimates" in w for w in rick["warnings"])


def test_search_by_alias_and_id(client):
    for origin in ("Uttara", "UTTARA_NORTH", "uttara north"):
        assert data(search(client, origin=origin))["routes"], origin
    assert data(search(client, origin="Mirpur", destination="Farmgate"))["origin"]["hub_id"] == "MIRPUR_10"


# ---- constraints ----------------------------------------------------------------------------------
def test_avoid_hubs_works(client, engine):
    add_rickshaw(engine)
    plain = data(search(client))
    assert any("MIRPUR_10" in (l["from_hub"], l["to_hub"]) for o in plain["routes"] for l in o["legs"])
    avoided = data(search(client, avoid_hubs=["Mirpur"]))  # alias of MIRPUR_10
    assert avoided["routes"]
    assert all("MIRPUR_10" not in (l["from_hub"], l["to_hub"]) for o in avoided["routes"] for l in o["legs"])


def test_avoid_hubs_can_remove_every_route_with_suggestion(client):
    d = data(search(client, avoid_hubs=["Mirpur 10"]))
    assert d["routes"] == []
    reason = d["no_route_reason"]
    assert reason["code"] == "NO_ROUTE_WITHIN_CONSTRAINTS"
    assert {"type": "remove_avoided_hub", "message": "A route exists if MIRPUR_10 is not avoided.",
            "value": "MIRPUR_10"} in reason["suggestions"]


def test_avoid_modes_works(client, engine):
    add_rickshaw(engine)
    d = data(search(client, avoid_modes=["rickshaw"]))
    assert d["routes"] and all(l["mode"] != "rickshaw" for o in d["routes"] for l in o["legs"])
    no_metro = data(search(client, avoid_modes=["mrt"]))
    assert all(l["mode"] == "rickshaw" for o in no_metro["routes"] for l in o["legs"])


def test_budget_warning(client, engine):
    add_rickshaw(engine)
    d = data(search(client, budget_bdt=50, optimize="cheapest"))
    cheap, rick = d["routes"][0], next(o for o in d["routes"] if o["legs"][0]["mode"] == "rickshaw")
    assert cheap["within_budget"] is True and rick["within_budget"] is False
    assert any(w.startswith("OVER_BUDGET") for w in rick["warnings"])
    assert not any(w.startswith("OVER_BUDGET") for w in cheap["warnings"])


def test_strict_budget_discards_and_explains(client, engine):
    add_rickshaw(engine)
    d = data(search(client, budget_bdt=50, strict_budget=True))
    assert d["routes"] and all(o["within_budget"] and o["total_regular_fare_min_bdt"] <= 50 for o in d["routes"])
    nothing = data(search(client, budget_bdt=5, strict_budget=True))
    assert nothing["routes"] == []
    assert nothing["no_route_reason"]["code"] == "NO_ROUTE_WITHIN_CONSTRAINTS"
    assert any(s["type"] == "increase_budget" and s["value"] == 22 for s in nothing["no_route_reason"]["suggestions"])


# ---- student fares ----------------------------------------------------------------------------------
def test_student_range_equals_regular_when_no_discount(client):
    d = data(search(client, prefer_student_fare=True))
    for o in d["routes"]:
        assert o["total_student_fare_min_bdt"] == o["total_regular_fare_min_bdt"]
        assert o["total_student_fare_max_bdt"] == o["total_regular_fare_max_bdt"]
        assert all(l["student_discount_applies"] is False for l in o["legs"])


def test_student_discount_applies_on_buses_only(client, engine):
    add_open_bus(engine)
    add_rickshaw(engine)
    d = data(search(client, prefer_student_fare=True, optimize="cheapest"))
    bus = next(o for o in d["routes"] if o["legs"][0]["mode"] == "local_bus")
    assert (bus["total_regular_fare_min_bdt"], bus["total_student_fare_min_bdt"]) == (30, 15)
    assert bus["legs"][0]["student_discount_applies"] is True
    rick = next(o for o in d["routes"] if o["legs"][0]["mode"] == "rickshaw")
    assert rick["total_student_fare_min_bdt"] == rick["total_regular_fare_min_bdt"]
    assert rick["legs"][0]["student_discount_applies"] is False


# ---- errors ------------------------------------------------------------------------------------------
def test_unknown_hub(client):
    err = error(search(client, destination="Narnia"), 404, "UNKNOWN_HUB")
    assert err["field"] == "destination"
    assert error(search(client, origin="Narnia"), 404, "UNKNOWN_HUB")["field"] == "origin"


def test_ambiguous_hub_lists_candidates(client, engine):
    add(engine, Hub(hub_id="MIRPUR_11", name="Mirpur 11"), Hub(hub_id="MIRPUR_12", name="Mirpur 12"))
    err = error(search(client, origin="Mirpu"), 422, "AMBIGUOUS_HUB")
    assert {c["hub_id"] for c in err["details"]["candidates"]} >= {"MIRPUR_11", "MIRPUR_12"}


def test_invalid_scenario(client):
    error(search(client, scenario="snow"), 422, "INVALID_SCENARIO")


def test_validation_errors_use_the_envelope(client):
    for body in ({"origin": "A"}, {}, {"origin": "A", "destination": "B", "budget_bdt": -1},
                 {"origin": "A", "destination": "B", "optimize": "teleport"},
                 {"origin": "A", "destination": "B", "avoid_hubs": "Shahbagh"}):
        r = client.post("/api/trips/search", json=body)
        assert r.status_code == 422, body
        assert r.json()["ok"] is False and r.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_same_origin_destination(client):
    error(search(client, origin="Farmgate", destination="Farm gate"), 422, "SAME_ORIGIN_DESTINATION")


def test_no_route_found_is_a_successful_empty_result(client):
    # the metro edges are forward-only (no direction data) and the bus edge is closed
    d = data(search(client, origin="Farmgate", destination="Uttara North"))
    assert d["routes"] == []
    assert d["no_route_reason"]["code"] == "NO_ROUTE_FOUND" and d["no_route_reason"]["suggestions"] == []
    assert any(w.startswith("NO_ROUTE_FOUND") for w in d["warnings"])


# ---- natural language ----------------------------------------------------------------------------------
def test_query_text_without_nlp_service(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.nlp_service", None)  # force ImportError
    r = client.post("/api/trips/search", json={
        "query_text": "Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao", "session_id": "DEMO_SESSION"})
    err = error(r, 501, "NLP_NOT_IMPLEMENTED")
    fields = err["details"]["suggested_fields"]
    assert {"origin", "destination", "budget_bdt", "prefer_student_fare", "avoid_hubs", "avoid_modes",
            "optimize", "scenario"} <= set(fields)
    assert fields["session_id"] == "DEMO_SESSION"


def test_query_text_never_guesses_a_route_from_raw_text(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.nlp_service", None)
    r = client.post("/api/trips/search", json={"query_text": "Uttara North to Farmgate"})
    assert r.status_code == 501 and r.json()["data"] is None


def _fake_nlp(monkeypatch, parse):
    module = types.ModuleType("app.services.nlp_service")
    module.parse_query = parse
    monkeypatch.setitem(sys.modules, "app.services.nlp_service", module)


def test_query_text_uses_nlp_service_when_available(client, monkeypatch):
    seen = {}

    def parse_query(text, session_id=None):
        seen.update(text=text, session_id=session_id)
        return {"origin": "Uttara", "destination": "Farmgate", "prefer_student_fare": True, "budget_bdt": 80,
                "junk_field": 1}

    _fake_nlp(monkeypatch, parse_query)
    d = data(client.post("/api/trips/search", json={"query_text": "uttara theke farmgate", "session_id": "S"}))
    assert seen == {"text": "uttara theke farmgate", "session_id": "S"}
    assert d["query_echo"]["parsed_from_text"] is True and d["query_echo"]["prefer_student_fare"] is True
    assert d["query_echo"]["budget_bdt"] == 80 and "junk_field" not in d["query_echo"]
    assert d["routes"] and d["routes"][0]["legs"][0]["from_hub"] == "UTTARA_NORTH"


def test_explicit_fields_beat_parsed_fields(client, monkeypatch):
    _fake_nlp(monkeypatch, lambda text, session_id=None: {"origin": "Uttara", "destination": "Farmgate", "budget_bdt": 80})
    d = data(client.post("/api/trips/search", json={"query_text": "x", "budget_bdt": 5, "optimize": "cheapest"}))
    assert d["query_echo"]["budget_bdt"] == 5 and d["query_echo"]["optimize"] == "cheapest"


def test_structured_fields_win_over_query_text(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.nlp_service", None)
    d = data(search(client, query_text="ignored"))
    assert d["routes"] and d["query_echo"]["parsed_from_text"] is False


def test_nlp_failures_are_enveloped(client, monkeypatch):
    _fake_nlp(monkeypatch, lambda text, session_id=None: {"origin": "Uttara"})
    error(client.post("/api/trips/search", json={"query_text": "x"}), 422, "NLP_INCOMPLETE_QUERY")

    def boom(text, session_id=None):
        raise RuntimeError("model down")

    _fake_nlp(monkeypatch, boom)
    error(client.post("/api/trips/search", json={"query_text": "x"}), 422, "NLP_PARSE_FAILED")
    _fake_nlp(monkeypatch, lambda text, session_id=None: {"error": {"message": "not about transit"}})
    assert error(client.post("/api/trips/search", json={"query_text": "x"}), 422, "NLP_PARSE_FAILED")["message"] == "not about transit"
    module = types.ModuleType("app.services.nlp_service")  # present but without parse_query
    monkeypatch.setitem(sys.modules, "app.services.nlp_service", module)
    error(client.post("/api/trips/search", json={"query_text": "x"}), 501, "NLP_NOT_IMPLEMENTED")


# ---- sessions & simulation ---------------------------------------------------------------------------------
def test_simulation_applies_only_to_its_session(client, engine):
    add(engine, Simulation(scenario_id="sim1", session_id="S1", scenario_type="blocked_intersection",
                           zone_hub="MIRPUR_10", effects_json={"close_zone_edges": True}))
    mine = data(search(client, session_id="S1"))
    other = data(search(client, session_id="S2"))
    anon = data(search(client))
    assert mine["routes"] == [] and mine["no_route_reason"]["code"] == "NO_ROUTE_FOUND"
    assert mine["active_scenario"]["scenario_id"] == "sim1" and mine["active_scenario"]["zone_hub"] == "MIRPUR_10"
    assert other["routes"] and anon["routes"] and other["active_scenario"] is None
    assert mine["disruptions"]["simulation_scenario_id"] == "sim1"


def test_simulation_fare_scenario_is_reported(client, engine):
    add_rickshaw(engine)
    add(engine, FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=1, normal_high_bdt=2,
                              rain_low_bdt=300, rain_high_bdt=400))
    add(engine, Simulation(scenario_id="rain1", session_id="S1", scenario_type="heavy_rain", effects_json={}))
    d = data(search(client, session_id="S1", avoid_modes=["mrt"]))
    assert d["scenario"] == "rain" and d["active_scenario"]["scenario_type"] == "heavy_rain"
    assert d["active_scenario"]["effects"]["fare_scenario"] == "rain" and d["active_scenario"]["effects"]["time_scenario"] == "rain"
    assert data(search(client, avoid_modes=["mrt"]))["scenario"] == "normal"


# ---- no fake routes ------------------------------------------------------------------------------------------
def test_routes_only_use_real_edges_and_hubs(client, engine):
    add_rickshaw(engine)
    add_open_bus(engine)
    with Session(engine) as s:
        from sqlmodel import select
        edges = {e.route_id: {e.origin_hub, e.destination_hub} for e in s.exec(select(RouteEdge)).all()}
        hub_ids = {h.hub_id for h in s.exec(select(Hub)).all()}
    for optimize in ("cheapest", "fastest", "fewest_transfers", "balanced"):
        for o in data(search(client, optimize=optimize))["routes"]:
            assert o["grounding"]["valid"] is True
            assert o["legs"][0]["from_hub"] == "UTTARA_NORTH" and o["legs"][-1]["to_hub"] == "FARMGATE"
            for leg in o["legs"]:
                assert leg["route_id"] in edges and {leg["from_hub"], leg["to_hub"]} == edges[leg["route_id"]]
                assert {leg["from_hub"], leg["to_hub"]} <= hub_ids


def test_search_with_closed_edge_never_uses_it(client):
    for o in data(search(client, origin="Farmgate", destination="Uttara North"))["routes"]:
        assert all(l["route_id"] != "LOCAL_BUS_FARMGATE_UTTARA_NORTH" for l in o["legs"])


# ---- /api/fares/estimate -----------------------------------------------------------------------------------
def estimate(client, **body):
    body.setdefault("scenario", "normal")
    return client.post("/api/fares/estimate", json=body)


def test_estimate_from_cached_route_option(client, engine):
    add_rickshaw(engine)
    routes = data(search(client, session_id="S1", optimize="fastest"))["routes"]
    option = next(o for o in routes if o["legs"][0]["mode"] == "rickshaw")
    d = data(estimate(client, route_option_id=option["route_option_id"], session_id="S1"))
    assert d["legs_source"] == "cached_route_option" and d["route_option_id"] == option["route_option_id"]
    assert (d["total_regular_fare_min_bdt"], d["total_regular_fare_max_bdt"]) == (100, 150)
    assert d["legs"][0]["student_discount_applies"] is False and d["currency"] == "BDT"
    assert set(d) >= {"total_student_fare_min_bdt", "total_student_fare_max_bdt", "legs", "warnings",
                      "student_discount_applies_any_leg"}


def test_estimate_option_cache_is_per_session(client):
    option = data(search(client, session_id="S1"))["routes"][0]["route_option_id"]
    error(estimate(client, route_option_id=option, session_id="S2"), 404, "ROUTE_OPTION_NOT_FOUND")
    error(estimate(client, route_option_id="OPT_NOPE_001"), 404, "ROUTE_OPTION_NOT_FOUND")
    data(search(client))  # no session id is its own bucket
    data(estimate(client, route_option_id=option))


def test_estimate_from_explicit_legs_prices_a_metro_ride_once(client):
    d = data(estimate(client, legs=[{"route_id": MRT_U_M, "mode": "mrt"}, {"route_id": MRT_M_F}]))
    assert d["legs_source"] == "request_legs"
    assert (d["total_regular_fare_min_bdt"], d["total_regular_fare_max_bdt"]) == (22, 22)  # not 11 + 33
    assert [l["regular_fare_min_bdt"] for l in d["legs"]] == [22, 0]
    reversed_order = data(estimate(client, legs=[{"route_id": MRT_M_F}, {"route_id": MRT_U_M}]))
    assert reversed_order["total_regular_fare_min_bdt"] == 22  # oriented by the shared hub


def test_estimate_scenario_does_not_change_metro(client):
    for scenario in ("normal", "rain", "peak_plus_rain", "detour", "festive_holiday"):
        d = data(estimate(client, legs=[{"route_id": MRT_U_M}], scenario=scenario))
        assert (d["total_regular_fare_min_bdt"], d["total_regular_fare_max_bdt"]) == (11, 11)


def test_estimate_student_flag(client, engine):
    add_open_bus(engine)
    legs = [{"route_id": "LOCAL_BUS_UTTARA_NORTH_FARMGATE", "mode": "local_bus"}]
    on = data(estimate(client, legs=legs, prefer_student_fare=True))
    assert (on["total_regular_fare_min_bdt"], on["total_student_fare_min_bdt"]) == (30, 15)
    assert on["legs"][0]["student_discount_applies"] is True and on["student_discount_applies_any_leg"] is True
    off = data(estimate(client, legs=legs, prefer_student_fare=False))
    assert off["total_student_fare_min_bdt"] == 30 and off["legs"][0]["student_discount_applies"] is False
    metro = data(estimate(client, legs=[{"route_id": MRT_U_M}], prefer_student_fare=True))
    assert metro["total_student_fare_min_bdt"] == metro["total_regular_fare_min_bdt"] == 11


def test_estimate_never_invents_legs(client):
    err = error(estimate(client, legs=[{"route_id": "MRT6_MADE_UP"}]), 404, "ROUTE_NOT_FOUND")
    assert err["details"]["route_id"] == "MRT6_MADE_UP"
    error(estimate(client, legs=[{"route_id": MRT_U_M, "mode": "rickshaw"}]), 422, "LEG_MODE_MISMATCH")
    for body in ({}, {"legs": []}, {"route_option_id": None}):
        r = client.post("/api/fares/estimate", json=body)
        assert r.status_code == 422 and r.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_estimate_invalid_scenario_and_unpriceable_leg(client, engine):
    error(estimate(client, legs=[{"route_id": MRT_U_M}], scenario="snow"), 422, "INVALID_SCENARIO")
    add(engine, Hub(hub_id="LONELY", name="Lonely"),
        RouteEdge(route_id="MRT6_LONELY_FARMGATE", mode="mrt", origin_hub="LONELY", destination_hub="FARMGATE"))
    err = error(estimate(client, legs=[{"route_id": "MRT6_LONELY_FARMGATE"}]), 422, "FARE_CALCULATION_FAILED")
    assert err["details"]["route_id"] == "MRT6_LONELY_FARMGATE" and err["details"]["code"] == "METRO_FARE_UNKNOWN"


def test_estimate_warns_about_estimates_and_ignored_legs(client, engine):
    add_rickshaw(engine)
    option = next(o for o in data(search(client, session_id="S1"))["routes"] if o["legs"][0]["mode"] == "rickshaw")
    d = data(estimate(client, route_option_id=option["route_option_id"], session_id="S1",
                      legs=[{"route_id": MRT_U_M}]))
    assert d["legs_source"] == "cached_route_option" and d["total_regular_fare_min_bdt"] == 100
    assert any(w.startswith("LEGS_IGNORED") for w in d["warnings"]) and any("estimates" in w for w in d["warnings"])


def test_estimate_uses_session_simulation_scenario(client, engine):
    add_rickshaw(engine)
    add(engine, FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=1,
                              normal_high_bdt=2, rain_low_bdt=300, rain_high_bdt=400),
        Simulation(scenario_id="r", session_id="S1", scenario_type="heavy_rain", effects_json={}))
    legs = [{"route_id": "RICKSHAW_FARMGATE_UTTARA_NORTH"}]
    mine = data(estimate(client, legs=legs, session_id="S1"))
    assert mine["scenario"] == "rain" and mine["total_regular_fare_min_bdt"] != 100
    assert data(estimate(client, legs=legs, session_id="S2"))["scenario"] == "normal"
    clash = data(estimate(client, legs=legs, session_id="S1", scenario="detour"))
    assert clash["scenario"] == "detour" and any(w.startswith("SCENARIO_CONFLICT") for w in clash["warnings"])
