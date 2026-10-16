"""Per-session simulation: effects, rerouting, before/after, events, and isolation between sessions.

Fixture network + extras added here: a symmetric rickshaw FARMGATE<->UTTARA_NORTH with explicit normal / rain /
peak / detour fares and times, so rerouting around a blocked MIRPUR_10 is possible.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.enums import ScenarioType
from app.models import Event, FareMatrixRow, Hub, RouteEdge, Simulation, utcnow
from app.responses import ApiError
from app.services import routing_service as rs
from app.services import search_cache
from app.services import simulation_service as sims
from app.tests.helpers import MRT_M_F, MRT_U_M, USER2, headers_for, user_by_email

RICK = "RICKSHAW_FARMGATE_UTTARA_NORTH"
BUS = "LOCAL_BUS_FARMGATE_UTTARA_NORTH"  # closed by the dataset itself


@pytest.fixture
def world(seeded):
    s = seeded
    s.add(RouteEdge(route_id=RICK, mode="rickshaw", origin_hub="FARMGATE", destination_hub="UTTARA_NORTH",
                    direction="unknown", source_dataset="dhaka_transport_fares", road_distance_km=5.0,
                    time_normal_min=30, time_rain_min=45, time_peak_min=40))
    s.add(FareMatrixRow(
        mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=100, normal_high_bdt=150,
        rain_low_bdt=160, rain_high_bdt=240, peak_office_hour_low_bdt=120, peak_office_hour_high_bdt=180,
        blockage_construction_waterlog_detour_low_bdt=200, blockage_construction_waterlog_detour_high_bdt=300,
        regular_holiday_low_bdt=90, regular_holiday_high_bdt=140, festive_holiday_low_bdt=150, festive_holiday_high_bdt=220))
    s.get(RouteEdge, MRT_U_M).time_normal_min = 10   # give the metro segments dataset times (the fixture has none)
    s.get(RouteEdge, MRT_M_F).time_normal_min = 12
    s.commit()
    search_cache.clear()
    yield s
    search_cache.clear()


def rickshaw_route(s, session_id=None, scenario="normal", **kw):
    res = rs.search_routes(s, "Uttara North", "Farmgate", avoid_modes=["mrt"], optimize="cheapest",
                           session_id=session_id, scenario=scenario, **kw)
    return res["routes"][0]


def edge_snapshot(s):
    s.expire_all()
    return [(e.route_id, e.status, e.closed_reason, e.time_normal_min, e.flood_prone) for e in
            s.exec(select(RouteEdge).order_by(RouteEdge.route_id)).all()]


# ---- session scoping ------------------------------------------------------------------------------------
def test_simulation_is_session_scoped(world):
    sims.apply_simulation(world, "A", "heavy_rain")
    assert sims.get_active_scenario(world, "A").scenario_type == "heavy_rain"
    assert sims.get_active_scenario(world, "B") is None and sims.get_active_scenario(world, None) is None
    assert sims.get_scenario_effects(world, "B") is None
    assert rs.build_graph(world, "A").scenario == "rain"
    assert rs.build_graph(world, "B").scenario == "normal" and rs.build_graph(world).scenario == "normal"


def test_session_a_rain_does_not_affect_session_b(world):
    sims.apply_simulation(world, "A", "heavy_rain")
    a, b, anon = (rickshaw_route(world, sid) for sid in ("A", "B", None))
    assert a["scenario"] == "rain" and b["scenario"] == anon["scenario"] == "normal"
    assert (b["total_regular_fare_min_bdt"], b["total_regular_fare_max_bdt"]) == (100, 150)
    assert (anon["total_regular_fare_min_bdt"], anon["total_regular_fare_max_bdt"]) == (100, 150)


def test_one_active_simulation_per_session(world):
    first = sims.apply_simulation(world, "A", "heavy_rain")
    second = sims.apply_simulation(world, "A", "peak_rush_hour")
    active = world.exec(select(Simulation).where(Simulation.session_id == "A", Simulation.status == "active")).all()
    assert [s.scenario_id for s in active] == [second.scenario_id]
    assert world.get(Simulation, first.scenario_id).status == "reset" and world.get(Simulation, first.scenario_id).reset_at
    assert sims.get_active_scenario(world, "A").scenario_type == "peak_rush_hour"


# ---- scenario effects ------------------------------------------------------------------------------------------
def test_heavy_rain_changes_rickshaw_range_and_time_to_rain(world):
    before = rickshaw_route(world, "A")
    sims.apply_simulation(world, "A", "heavy_rain")
    after = rickshaw_route(world, "A")
    assert (before["total_regular_fare_min_bdt"], before["total_regular_fare_max_bdt"]) == (100, 150)
    assert (after["total_regular_fare_min_bdt"], after["total_regular_fare_max_bdt"]) == (160, 240)  # explicit rain columns
    assert after["total_regular_fare_min_bdt"] < after["total_regular_fare_max_bdt"]  # still a range
    assert (before["total_duration_min"], after["total_duration_min"]) == (30, 45)  # Time_Rain_min
    assert after["scenario"] == "rain"


def test_peak_rush_hour_uses_peak_fare_and_time(world):
    sims.apply_simulation(world, "A", "peak_rush_hour")
    r = rickshaw_route(world, "A")
    assert r["scenario"] == "peak" and (r["total_regular_fare_min_bdt"], r["total_regular_fare_max_bdt"]) == (120, 180)
    assert r["total_duration_min"] == 40 and r["total_transfer_wait_min"] == 0
    eff = sims.get_scenario_effects(world, "A")
    assert eff.fare_scenario == "peak" and eff.extra_transfer_wait_min == 0 and eff.closed_route_ids == []


def test_holidays_change_only_the_fare_scenario(world):
    for kind, expected in (("regular_holiday", (90, 140)), ("festive_holiday", (150, 220))):
        sims.apply_simulation(world, "H", kind)
        r = rickshaw_route(world, "H")
        assert r["scenario"] == kind and (r["total_regular_fare_min_bdt"], r["total_regular_fare_max_bdt"]) == expected
        eff = sims.get_scenario_effects(world, "H")
        assert eff.closed_route_ids == [] and eff.time_scenario is None and eff.warnings == []
        assert r["total_duration_min"] == 30  # no time effect


def test_blocked_intersection_closes_non_walk_edges_touching_the_hub(world):
    world.add(RouteEdge(route_id="WALK_MIRPUR_10_FARMGATE", mode="walk", origin_hub="MIRPUR_10", destination_hub="FARMGATE",
                        direction="bidirectional", time_normal_min=20))
    world.commit()
    res = sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="Mirpur")  # alias resolves
    eff = res.effects
    assert res.zone_hub == "MIRPUR_10" and eff["fare_scenario"] == "detour"
    assert eff["closed_route_ids"] == sorted([MRT_U_M, MRT_M_F])  # the walk edge and untouched edges stay
    graph = rs.build_graph(world, "A")
    assert set(graph.closed) - {BUS} == {MRT_U_M, MRT_M_F}
    assert all("simulation" in why and "MIRPUR_10" in why for r, why in graph.closed.items() if r != BUS)
    assert any(e.route_id == "WALK_MIRPUR_10_FARMGATE" for e in graph.edges)
    assert any(e.route_id == RICK for e in graph.edges)
    other = rs.build_graph(world, "B")
    assert MRT_M_F not in other.closed


def test_blocked_intersection_reroutes_and_uses_detour_fares(world):
    sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10")
    res = rs.search_routes(world, "Uttara North", "Farmgate", session_id="A")
    assert res["routes"] and all(l["mode"] == "rickshaw" for o in res["routes"] for l in o["legs"])
    best = res["routes"][0]
    assert best["scenario"] == "detour" and (best["total_regular_fare_min_bdt"], best["total_regular_fare_max_bdt"]) == (200, 300)
    normal = rs.search_routes(world, "Uttara North", "Farmgate")
    assert any(l["mode"] == "mrt" for o in normal["routes"] for l in o["legs"])


def test_blocked_intersection_requires_a_zone_hub(world):
    with pytest.raises(ApiError) as e:
        sims.apply_simulation(world, "A", "blocked_intersection")
    assert e.value.code == "ZONE_HUB_REQUIRED"
    with pytest.raises(ApiError) as e:
        sims.apply_simulation(world, "A", "heavy_rain", zone_hub="Narnia")
    assert e.value.code == "UNKNOWN_HUB"
    assert sims.get_active_scenario(world, "A") is None  # failed attempts leave nothing behind


def test_heavy_rain_closes_only_flagged_flood_prone_edges_in_the_zone(world):
    res = sims.apply_simulation(world, "A", "heavy_rain", zone_hub="MIRPUR_10")
    assert res.effects["closed_route_ids"] == [] and any(w.startswith("NO_FLOOD_PRONE_DATA") for w in res.effects["warnings"])
    world.get(RouteEdge, MRT_M_F).flood_prone = True       # dataset flag, in the zone
    world.get(RouteEdge, RICK).flood_prone = True          # flagged, but outside the zone
    world.commit()
    sims.apply_simulation(world, "A", "heavy_rain", zone_hub="MIRPUR_10")
    assert sims.get_scenario_effects(world, "A").closed_route_ids == [MRT_M_F]
    assert set(rs.build_graph(world, "A").closed) - {BUS} == {MRT_M_F}
    assert rs.build_graph(world, "B").closed.keys() == {BUS}  # only the dataset-closed bus
    sims.apply_simulation(world, "C", "heavy_rain")  # no zone: every flagged edge
    assert sims.get_scenario_effects(world, "C").closed_route_ids == sorted([MRT_M_F, RICK])


def test_no_closure_is_invented(world):
    for kind in ("heavy_rain", "peak_rush_hour", "regular_holiday", "festive_holiday"):
        sims.apply_simulation(world, "S", kind)
        assert sims.get_scenario_effects(world, "S").closed_route_ids == [], kind
    assert set(rs.build_graph(world, "S").closed) == {"LOCAL_BUS_FARMGATE_UTTARA_NORTH"}


# ---- no global mutation -------------------------------------------------------------------------------------------
def test_simulation_never_mutates_route_edges(world):
    before = edge_snapshot(world)
    for kind, zone in (("blocked_intersection", "MIRPUR_10"), ("heavy_rain", "MIRPUR_10"), ("peak_rush_hour", None)):
        sims.apply_simulation(world, "A", kind, zone_hub=zone)
        rs.search_routes(world, "Uttara North", "Farmgate", session_id="A")
        rs.build_graph(world, "A")
    sims.reset_simulation(world, "A")
    assert edge_snapshot(world) == before


# ---- missing time data -----------------------------------------------------------------------------------------------
def test_missing_time_data_warns_instead_of_inventing_numbers(world):
    world.get(RouteEdge, MRT_M_F).time_normal_min = None  # a segment with no time at all
    world.commit()
    res = sims.apply_simulation(world, "A", "heavy_rain")
    warn = next(w for w in res.effects["warnings"] if w.startswith("TIME_RAIN_DATA_MISSING"))
    assert "no time multiplier is invented" in warn and "3 of 4" in warn  # 3 fixture edges lack a rain time
    assert res.effects["time_overrides"] == {RICK: 45.0}                    # only dataset-provided times
    graph = rs.build_graph(world, "A")
    metro = next(e for e in graph.edges if e.route_id == MRT_M_F)
    assert metro.time_min is None                                           # null, not a made-up number
    peak = sims.apply_simulation(world, "A", "peak_rush_hour")
    assert any(w.startswith("TIME_PEAK_DATA_MISSING") for w in peak.effects["warnings"])
    blocked = sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10")
    assert any(w.startswith("TIME_DETOUR_DATA_MISSING") for w in blocked.effects["warnings"])
    assert blocked.effects["time_overrides"] == {}


def test_rain_time_missing_falls_back_to_normal_time_with_leg_warning(world):
    world.get(RouteEdge, RICK).time_rain_min = None
    world.commit()
    sims.apply_simulation(world, "A", "heavy_rain")
    edge = next(e for e in rs.build_graph(world, "A").edges if e.route_id == RICK)
    assert edge.time_min == 30 and any(w.startswith("TIME_RAIN_DATA_MISSING") for w in edge.warnings)
    leg = rickshaw_route(world, "A")["legs"][0]
    assert leg["travel_time_min"] == 30 and any(w.startswith("TIME_RAIN_DATA_MISSING") for w in leg["warnings"])


def test_extra_transfer_wait_is_zero_without_config(world):
    for kind in ScenarioType:
        zone = "MIRPUR_10" if kind == ScenarioType.blocked_intersection else None
        assert sims.apply_simulation(world, "T", kind.value, zone).effects["extra_transfer_wait_min"] == 0


# ---- reset ----------------------------------------------------------------------------------------------------------------
def test_reset_restores_routing(world):
    baseline = rs.search_routes(world, "Uttara North", "Farmgate", session_id="A")
    sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10")
    blocked = rs.search_routes(world, "Uttara North", "Farmgate", session_id="A")
    assert [o["legs"][0]["mode"] for o in blocked["routes"]] != [o["legs"][0]["mode"] for o in baseline["routes"]]
    res = sims.reset_simulation(world, "A")
    assert res.status == "reset" and res.scenario_type == "blocked_intersection"
    restored = rs.search_routes(world, "Uttara North", "Farmgate", session_id="A")
    assert [o["route_option_id"] for o in restored["routes"]] == [o["route_option_id"] for o in baseline["routes"]]
    assert restored["routes"] == baseline["routes"] or restored["scenario"] == "normal"
    assert sims.get_active_scenario(world, "A") is None and restored["disruptions"]["simulation_scenario_id"] is None


def test_reset_without_an_active_simulation_is_harmless(world):
    res = sims.reset_simulation(world, "NOBODY")
    assert res.status == "none" and res.scenario_id is None
    assert world.exec(select(Event).where(Event.event_type == "simulation.reset")).all() == []


def test_reset_only_affects_its_own_session(world):
    sims.apply_simulation(world, "A", "heavy_rain")
    sims.apply_simulation(world, "B", "heavy_rain")
    sims.reset_simulation(world, "A")
    assert sims.get_active_scenario(world, "A") is None and sims.get_active_scenario(world, "B") is not None


def test_expired_simulation_is_ignored(world):
    res = sims.apply_simulation(world, "A", "heavy_rain")
    sim = world.get(Simulation, res.scenario_id)
    sim.expires_at = utcnow() - timedelta(seconds=1)
    world.add(sim)
    world.commit()
    assert sims.get_active_scenario(world, "A") is None and rs.build_graph(world, "A").scenario == "normal"


# ---- before / after ------------------------------------------------------------------------------------------------------------
def test_before_after_comparison_returns_ranges(world):
    res = sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10",
                                origin="Uttara North", destination="Farmgate")
    c = res.before_after_comparison
    assert c["available"] and c["rerouted"] is True and c["note"] is None
    assert c["original_route_id"] == f"{MRT_U_M}+{MRT_M_F}" and c["new_route_id"] == RICK
    assert (c["original_fare_min"], c["original_fare_max"]) == (22, 22)          # official metro fare, fixed
    assert (c["new_fare_min"], c["new_fare_max"]) == (200, 300)                  # detour range
    assert c["new_fare_min"] < c["new_fare_max"]
    assert (c["added_fare_min"], c["added_fare_max"]) == (178, 278)
    assert (c["original_duration_min"], c["new_duration_min"], c["added_time_min"]) == (22, 30, 8)
    assert {s["route_id"] for s in res.broken_segments} == {MRT_U_M, MRT_M_F}
    assert [r["legs"][0]["route_id"] for r in res.recalculated_routes] == [RICK]
    assert res.before_after_comparison["before"]["total_regular_fare_min_bdt"] == 22
    assert sims.get_active_scenario(world, "A") is not None


def test_comparison_with_times_computes_added_time(world):
    c = sims.compare_before_after(world, "A", "Farmgate", "Uttara North", False)  # no simulation: nothing changes
    assert c.available and c.rerouted is False and c.added_fare_min == 0
    sims.apply_simulation(world, "A", "heavy_rain")
    rain = sims.compare_before_after(world, "A", "Uttara North", "Farmgate", False)
    assert rain.rerouted is False  # metro still best; its fare and time do not change in rain
    assert (rain.added_fare_min, rain.added_fare_max, rain.added_time_min) == (0, 0, 0)
    avoid = rs.search_routes(world, "Uttara North", "Farmgate", avoid_modes=["mrt"], session_id="A")
    assert avoid["routes"][0]["total_duration_min"] - 30 == 15  # rain time 45 vs normal 30


def test_comparison_uses_the_sessions_last_search_and_student_flag(world):
    search_cache.remember_query("A", {"origin": "UTTARA_NORTH", "destination": "FARMGATE", "prefer_student_fare": True})
    res = sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10")
    c = res.before_after_comparison
    assert c["available"] and c["origin"] == "UTTARA_NORTH" and c["prefer_student_fare"] is True


def test_comparison_without_a_journey_says_so(world):
    res = sims.apply_simulation(world, "A", "heavy_rain")
    c = res.before_after_comparison
    assert c["available"] is False and c["note"].startswith("NO_JOURNEY") and res.recalculated_routes == []
    assert res.status == "active" and res.scenario_id.startswith("sim_")


def test_comparison_when_the_simulation_leaves_no_route(world):
    world.delete(world.get(RouteEdge, RICK))
    world.commit()
    c = sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10", origin="Uttara North",
                              destination="Farmgate").before_after_comparison
    assert c["available"] and c["note"].startswith("NO_ROUTE_AFTER") and c["new_route_id"] is None
    assert c["original_fare_min"] == 22 and c["added_fare_min"] is None


def test_comparison_with_unknown_hubs(world):
    c = sims.compare_before_after(world, "A", "Narnia", "Farmgate")
    assert c.available is False and c.note.startswith("UNKNOWN_HUB")


# ---- events ---------------------------------------------------------------------------------------------------------------------
def test_events_are_published_and_session_scoped(world):
    start = world.exec(select(Event)).all()
    sims.apply_simulation(world, "A", "heavy_rain", user_id=3)
    sims.reset_simulation(world, "A", user_id=3)
    new = [e for e in world.exec(select(Event).order_by(Event.event_id)).all() if e not in start]
    assert [e.event_type for e in new] == ["simulation.applied", "route.recalculated", "simulation.reset", "route.recalculated"]
    assert all(e.session_id == "A" for e in new)
    assert new[0].payload_json["fare_scenario"] == "rain" and new[1].payload_json["reason"] == "simulation_applied"
    from app.services import event_service
    assert not any(e.event_type.startswith("simulation") for e in event_service.get_events(world, 0, "B", 500))
    assert [e.event_type for e in event_service.get_events(world, 0, "A", 500) if e.session_id == "A"][0] == "simulation.applied"


# ---- graph context -------------------------------------------------------------------------------------------------------------------
def test_build_session_graph_context(world):
    sims.apply_simulation(world, "A", "blocked_intersection", zone_hub="MIRPUR_10")
    ctx = sims.build_session_graph_context(world, "A")
    assert ctx.scenario.scenario_type == "blocked_intersection" and ctx.effects.zone_hub == "MIRPUR_10"
    assert set(ctx.graph.closed) >= {MRT_U_M, MRT_M_F}
    empty = sims.build_session_graph_context(world, "B")
    assert empty.scenario is None and empty.effects is None


def test_explicit_effects_json_still_overrides(world):
    world.add(Simulation(scenario_id="legacy", session_id="L", scenario_type="peak_rush_hour",
                         effects_json={"time_multiplier": 2.0}))
    world.commit()
    edge = next(e for e in rs.build_graph(world, "L").edges if e.route_id == RICK)
    assert edge.time_min == 80  # peak time 40 x explicit multiplier 2


# ---- HTTP -------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(client, engine):
    with Session(engine) as s:
        s.add(RouteEdge(route_id=RICK, mode="rickshaw", origin_hub="FARMGATE", destination_hub="UTTARA_NORTH",
                        direction="unknown", source_dataset="dhaka_transport_fares", road_distance_km=5.0,
                        time_normal_min=30, time_rain_min=45))
        s.add(FareMatrixRow(mode="rickshaw", from_hub="FARMGATE", to_hub="UTTARA_NORTH", normal_low_bdt=100,
                            normal_high_bdt=150, rain_low_bdt=160, rain_high_bdt=240,
                            blockage_construction_waterlog_detour_low_bdt=200, blockage_construction_waterlog_detour_high_bdt=300))
        s.commit()
        h = headers_for(s, USER2)
    return client, h


def test_api_requires_a_verified_user(client):
    for method, path, body in (("post", "/api/simulations", {"session_id": "A", "scenario_type": "heavy_rain"}),
                               ("post", "/api/simulations/reset", {"session_id": "A"}),
                               ("get", "/api/simulations/active?session_id=A", None)):
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 401 and r.json()["errors"][0]["code"] == "UNAUTHORIZED"


def test_api_apply_active_reset_flow(api, engine):
    client, h = api
    with Session(engine) as s:
        s.get(RouteEdge, MRT_U_M).time_normal_min = 10
        s.get(RouteEdge, MRT_M_F).time_normal_min = 12
        s.commit()
    trip = client.post("/api/trips/search", json={"origin": "Uttara North", "destination": "Farmgate", "session_id": "DEMO_SESSION"})
    assert trip.status_code == 200
    r = client.post("/api/simulations", json={"session_id": "DEMO_SESSION", "scenario_type": "blocked_intersection",
                                              "zone_hub": "MIRPUR_10"}, headers=h)
    body = r.json()
    assert r.status_code == 201 and body["ok"] is True and body["errors"] == []
    d = body["data"]
    assert set(d) >= {"scenario_id", "session_id", "scenario_type", "status", "effects", "broken_segments",
                      "recalculated_routes", "before_after_comparison"}
    assert d["status"] == "active" and d["session_id"] == "DEMO_SESSION"
    assert d["before_after_comparison"]["rerouted"] is True and d["before_after_comparison"]["new_fare_min"] == 200
    assert {s["route_id"] for s in d["broken_segments"]} == {MRT_U_M, MRT_M_F}  # from the session's last search
    assert set(d["effects"]) >= {"scenario_type", "fare_scenario", "zone_hub", "closed_route_ids", "time_overrides",
                                 "extra_transfer_wait_min", "warnings"}

    active = client.get("/api/simulations/active", params={"session_id": "DEMO_SESSION"}, headers=h).json()["data"]
    assert active["active"] is True and active["scenario_id"] == d["scenario_id"] and active["effects"]["fare_scenario"] == "detour"
    other = client.get("/api/simulations/active", params={"session_id": "OTHER"}, headers=h).json()["data"]
    assert other["active"] is False

    mine = client.post("/api/trips/search", json={"origin": "Uttara North", "destination": "Farmgate", "session_id": "DEMO_SESSION"}).json()["data"]
    assert mine["active_scenario"]["scenario_id"] == d["scenario_id"] and mine["routes"][0]["legs"][0]["mode"] == "rickshaw"
    theirs = client.post("/api/trips/search", json={"origin": "Uttara North", "destination": "Farmgate", "session_id": "OTHER"}).json()["data"]
    assert theirs["active_scenario"] is None and theirs["routes"][0]["legs"][0]["mode"] == "mrt"
    routes = {x["route_id"]: x for x in client.get("/api/transit/routes", params={"session_id": "DEMO_SESSION"}).json()["data"]}
    assert routes[MRT_M_F]["closed_by"] == "simulation"
    assert {x["route_id"]: x for x in client.get("/api/transit/routes").json()["data"]}[MRT_M_F]["status"] == "active"

    r = client.post("/api/simulations/reset", json={"session_id": "DEMO_SESSION"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "reset"
    assert client.get("/api/simulations/active", params={"session_id": "DEMO_SESSION"}, headers=h).json()["data"]["active"] is False
    again = client.post("/api/trips/search", json={"origin": "Uttara North", "destination": "Farmgate", "session_id": "DEMO_SESSION"}).json()["data"]
    assert again["routes"][0]["legs"][0]["mode"] == "mrt"
    assert client.post("/api/simulations/reset", json={"session_id": "DEMO_SESSION"}, headers=h).json()["data"]["status"] == "none"


def test_api_validation(api):
    client, h = api
    for body in ({}, {"session_id": "A"}, {"session_id": "A", "scenario_type": "tornado"}, {"session_id": "", "scenario_type": "heavy_rain"}):
        assert client.post("/api/simulations", json=body, headers=h).status_code == 422
    r = client.post("/api/simulations", json={"session_id": "A", "scenario_type": "blocked_intersection"}, headers=h)
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "ZONE_HUB_REQUIRED"
    r = client.post("/api/simulations", json={"session_id": "A", "scenario_type": "heavy_rain", "zone_hub": "Narnia"}, headers=h)
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "UNKNOWN_HUB"
    assert client.get("/api/simulations/active", headers=h).status_code == 422


def test_api_simulation_events_are_private_to_the_session(api):
    client, h = api
    client.post("/api/simulations", json={"session_id": "S1", "scenario_type": "heavy_rain"}, headers=h)
    public = client.get("/api/events").json()["data"]["events"]
    assert not any(e["event_type"].startswith("simulation") for e in public)
    mine = client.get("/api/events", params={"session_id": "S1"}).json()["data"]["events"]
    assert [e["event_type"] for e in mine if e["session_id"] == "S1"] == ["simulation.applied", "route.recalculated"]
