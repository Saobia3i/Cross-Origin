"""Routing tests on a synthetic graph (no production data). All hubs, edges, times and fares are made up.

            A ---- B ---- C            A-B  rickshaw 5 min / 30-40 BDT    B-C  cng 5 min / 30-40
            |\\          /|            A-B  bus 15 / 10                    B-C  bus 15 / 10
            | \\_______ / |            A-C  rickshaw 20 / 100-150          A-C  bus 60 / 25
            D ------------+            A-D  cng 12 / 60-80                D-C  cng 12 / 60-80
    (X has no coordinates and hangs off C by bus: C-X 10 min / 5 BDT; Z is unconnected.)
"""
import pytest
from sqlmodel import select

from app.enums import ReportStatus
from app.models import (
    FareMatrixRow, Hub, MetroFareMatrix, ModeRate, Report, RouteEdge, ScenarioMultiplier, Simulation, User,
    utcnow,
)
from app.services import fare_service as fs
from app.services import routing_service as rs
from app.services.seeder import SeedData, SeedResult, seed_fare_rules
from datetime import timedelta

# (route_id, mode, origin, destination, time_normal, fare_low, fare_high, extra)
EDGES = [
    ("RICK_A_B", "rickshaw", "A", "B", 5, 30, 40, {}),
    ("CNG_B_C", "cng", "B", "C", 5, 30, 40, {}),
    ("RICK_A_C", "rickshaw", "A", "C", 20, 100, 150, {}),
    ("BUS_A_C", "local_bus", "A", "C", 60, 25, 25, {}),
    ("BUS_A_B", "local_bus", "A", "B", 15, 10, 10, {}),
    ("BUS_B_C", "local_bus", "B", "C", 15, 10, 10, {}),
    ("CNG_A_D", "cng", "A", "D", 12, 60, 80, {}),
    ("CNG_D_C", "cng", "D", "C", 12, 60, 80, {}),
    ("BUS_C_X", "local_bus", "C", "X", 10, 5, 5, {}),
]
HUBS = {"A": (23.70, 90.40), "B": (23.72, 90.40), "C": (23.74, 90.40), "D": (23.72, 90.42), "X": (None, None),
        "Z": (23.80, 90.50)}


@pytest.fixture
def w(session):
    for hub_id, (lat, lon) in HUBS.items():
        session.add(Hub(hub_id=hub_id, name=f"Hub {hub_id}", aliases_json=[f"Alias {hub_id}"], latitude=lat, longitude=lon))
    session.commit()
    for rid, mode, o, d, t, lo, hi, extra in EDGES:
        session.add(RouteEdge(route_id=rid, mode=mode, origin_hub=o, destination_hub=d, direction="bidirectional",
                              time_normal_min=t, road_distance_km=2.0, **extra))
        session.add(FareMatrixRow(mode=mode, from_hub=o, to_hub=d, normal_low_bdt=lo, normal_high_bdt=hi,
                                  rain_low_bdt=lo * 2, rain_high_bdt=hi * 2))
    for mode, (b, k, m, lo, hi, speed) in {"rickshaw": (15, 25, 30, 0.85, 1.25, 9), "cng": (30, 38, 70, 0.9, 1.25, 22),
                                           "local_bus": (0, 2.45, 10, 1, 1, 14)}.items():
        session.add(ModeRate(mode=mode, base_fare_bdt=b, per_km_bdt=k, min_fare_bdt=m, low_band_multiplier=lo,
                             high_band_multiplier=hi, average_speed_kmh=speed))
        for scen in ("normal", "rain", "peak", "detour"):
            session.add(ScenarioMultiplier(mode=mode, scenario=scen, multiplier=1.0))
    session.add(User(email="r@x.local", name="r", password_hash="x"))
    session.commit()
    seed_fare_rules(session, SeedData("fixtures"), SeedResult("fixtures"))  # local_bus student 50% / min 10
    session.commit()
    return session


def ids(option):
    return [l["route_id"] for l in option["legs"]]


def by_label(result, label):
    return next(o for o in result["routes"] if o["rank_label"] == label or label in o["also_best_for"])


def search(w, o="A", d="C", **kw):
    return rs.search_routes(w, f"Hub {o}", f"Hub {d}", **kw)


def report(session, status="verified", route_ids=("BUS_A_B",), closed=False, extra=0, minutes=60, rid="rep1"):
    session.add(Report(report_id=rid, report_type="waterlogging", severity="heavy", description="d", reported_by=1,
                       half_life_min=60, expires_at=utcnow() + timedelta(minutes=minutes), status=status,
                       affected_route_ids_json=list(route_ids), edge_closed=closed, extra_time_min=extra))
    session.commit()


# ---- geometry & algorithm choice ---------------------------------------------------
def test_haversine():
    assert rs.haversine_km(0, 0, 0, 0) == 0
    assert rs.haversine_km(23.0, 90.0, 24.0, 90.0) == pytest.approx(111.19, abs=0.1)  # 1 degree of latitude
    assert rs.haversine_km(23.7, 90.4, 23.8, 90.5) == rs.haversine_km(23.8, 90.5, 23.7, 90.4)
    # longitude degrees shrink with latitude: not Euclidean on raw lat/lng
    assert rs.haversine_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)
    assert rs.haversine_km(60, 0, 60, 1) == pytest.approx(55.6, abs=0.3)


def test_dijkstra_fallback_when_a_hub_has_no_coordinates(w):
    graph = rs.build_graph(w)
    assert graph.has_all_coordinates is False  # X has none
    ctx = rs.make_context(graph, False)
    path = rs.a_star(graph, "A", "X", "fastest", ctx)
    assert path.algorithm == "dijkstra" and [e.route_id for e in path.edges][-1] == "BUS_C_X"
    assert rs.dijkstra(graph, "A", "X", "fastest", ctx).cost == pytest.approx(path.cost)


def test_a_star_used_with_full_coordinates_and_matches_dijkstra(w):
    graph = rs.build_graph(w, avoid_hubs=["X", "Z"])
    ctx = rs.make_context(graph, False)
    assert graph.has_all_coordinates
    a, d = rs.a_star(graph, "A", "C", "fastest", ctx), rs.dijkstra(graph, "A", "C", "fastest", ctx)
    assert a.algorithm == "a_star" and d.algorithm == "dijkstra"
    assert a.cost == pytest.approx(d.cost) == pytest.approx(10)
    assert [e.route_id for e in a.edges] == ["RICK_A_B", "CNG_B_C"]


# ---- objectives ----------------------------------------------------------------------
def test_cheapest_uses_fare_min(w):
    r = search(w, optimize="cheapest")
    best = r["routes"][0]
    assert best["rank_label"] == "cheapest" and ids(best) == ["BUS_A_B", "BUS_B_C"]
    assert best["total_regular_fare_min_bdt"] == 20 and best["total_regular_fare_max_bdt"] == 20


def test_fastest_uses_time(w):
    best = search(w, optimize="fastest")["routes"][0]
    assert best["rank_label"] == "fastest" and ids(best) == ["RICK_A_B", "CNG_B_C"]
    assert best["total_travel_time_min"] == 10 and best["total_duration_min"] == 10


def test_fewest_transfers_prefers_single_mode(w):
    best = search(w, optimize="fewest_transfers")["routes"][0]
    assert best["transfers"] == 0 and best["rank_label"] == "fewest_transfers"
    assert ids(best) == ["RICK_A_C"]  # 0 transfers, quickest of the 0-transfer options (20 vs 30 vs 60 min)


def test_balanced_returns_a_priced_timed_route(w):
    best = search(w, optimize="balanced")["routes"][0]
    assert best["rank_label"] == "balanced" or "balanced" in best["also_best_for"]
    assert best["total_regular_fare_min_bdt"] is not None and best["total_duration_min"] is not None


def test_fastest_excludes_edges_without_time_but_cheapest_may_use_them(w):
    rid = "NOTIME_A_Z"
    w.add(RouteEdge(route_id=rid, mode="car", origin_hub="A", destination_hub="Z", direction="bidirectional"))
    w.add(RouteEdge(route_id="BUS_Z_C", mode="local_bus", origin_hub="Z", destination_hub="C",
                    direction="bidirectional", time_normal_min=1, road_distance_km=1))
    w.add(FareMatrixRow(mode="car", from_hub="A", to_hub="Z", normal_low_bdt=1, normal_high_bdt=2))
    w.add(FareMatrixRow(mode="local_bus", from_hub="Z", to_hub="C", normal_low_bdt=1, normal_high_bdt=1))
    w.commit()
    graph = rs.build_graph(w)
    z_edge = next(e for e in graph.adj["A"] if e.route_id == rid)
    assert z_edge.time_min is None  # no dataset time, and "car" has no dataset speed to derive one
    ctx = rs.make_context(graph, False)
    cheap = rs.a_star(graph, "A", "C", "cheapest", ctx)
    assert [e.route_id for e in cheap.edges] == [rid, "BUS_Z_C"]  # fare 2, unknown time allowed
    fast = rs.a_star(graph, "A", "C", "fastest", ctx)
    assert rid not in [e.route_id for e in fast.edges]  # excluded from time ranking


def test_missing_time_is_derived_only_from_dataset_speed_and_distance(w):
    w.add(RouteEdge(route_id="DERIVE", mode="local_bus", origin_hub="A", destination_hub="Z",
                    direction="bidirectional", road_distance_km=14.0))
    w.add(RouteEdge(route_id="NOSPEED", mode="walk", origin_hub="A", destination_hub="Z", direction="bidirectional",
                    road_distance_km=1.0))
    w.add(RouteEdge(route_id="NODIST", mode="local_bus", origin_hub="D", destination_hub="X", direction="bidirectional"))
    w.commit()
    edges = {e.route_id: e for e in rs.build_graph(w).edges}
    assert edges["DERIVE"].time_min == pytest.approx(60.0)  # 14 km at the dataset's 14 km/h
    assert any(x.startswith("TIME_DERIVED") for x in edges["DERIVE"].warnings)
    assert edges["NOSPEED"].time_min is None  # walk has no dataset speed: stays null
    assert edges["NODIST"].time_min is None  # X has no coordinates and the edge has no distance


# ---- constraints -------------------------------------------------------------------------
def test_avoid_hub_removes_edges(w):
    r = search(w, optimize="cheapest", avoid_hubs=["Hub B"])
    assert r["routes"] and all("B" not in (l["from_hub"], l["to_hub"]) for o in r["routes"] for l in o["legs"])
    assert r["routes"][0]["total_regular_fare_min_bdt"] == 25  # direct bus
    graph = rs.build_graph(w, avoid_hubs=["B"])
    assert all("B" not in (e.from_hub, e.to_hub) for e in graph.edges)


def test_avoid_hub_never_removes_origin_or_destination(w):
    r = search(w, avoid_hubs=["A", "C"])
    assert r["routes"] and any("AVOID_HUB_IGNORED" in x for x in r["warnings"])


def test_avoid_mode_removes_edges(w):
    r = search(w, optimize="cheapest", avoid_modes=["local_bus"])
    assert all(l["mode"] != "local_bus" for o in r["routes"] for l in o["legs"])
    assert all(e.mode != "local_bus" for e in rs.build_graph(w, avoid_modes=["local_bus"]).edges)


def test_unknown_avoid_mode_is_ignored_with_warning(w):
    r = search(w, avoid_modes=["hovercraft"])
    assert r["routes"] and any("AVOID_MODE_IGNORED" in x for x in r["warnings"])


def test_budget_flags_and_strict_budget_discards(w):
    r = search(w, optimize="cheapest", budget_bdt=50)
    assert r["routes"][0]["within_budget"] is True
    assert any(o["within_budget"] is False for o in r["routes"])
    assert any("OVER_BUDGET" in x for o in r["routes"] for x in o["warnings"])
    strict = search(w, optimize="cheapest", budget_bdt=50, strict_budget=True)
    assert strict["routes"] and all(o["total_regular_fare_min_bdt"] <= 50 for o in strict["routes"])
    assert len(strict["routes"]) < len(r["routes"])


def test_strict_budget_too_low_gives_no_route_with_suggestion(w):
    r = search(w, optimize="cheapest", budget_bdt=5, strict_budget=True)
    assert r["routes"] == []
    reason = r["no_route_reason"]
    assert reason["code"] == "NO_ROUTE_WITHIN_CONSTRAINTS"
    assert any(s["type"] == "increase_budget" and s["value"] == 20 for s in reason["suggestions"])


def test_no_route_suggests_removing_avoided_hub(w):
    r = rs.search_routes(w, "Hub C", "Hub X", avoid_hubs=["Hub C", "Hub B"])  # C is the origin: ignored
    assert r["routes"]
    w.add(Hub(hub_id="M", name="Hub M", latitude=23.71, longitude=90.41))
    w.add(RouteEdge(route_id="BUS_C_M", mode="local_bus", origin_hub="C", destination_hub="M", direction="bidirectional",
                    time_normal_min=3))
    w.add(RouteEdge(route_id="BUS_M_Z", mode="local_bus", origin_hub="M", destination_hub="Z", direction="bidirectional",
                    time_normal_min=3))
    w.add(FareMatrixRow(mode="local_bus", from_hub="C", to_hub="M", normal_low_bdt=1, normal_high_bdt=1))
    w.add(FareMatrixRow(mode="local_bus", from_hub="M", to_hub="Z", normal_low_bdt=1, normal_high_bdt=1))
    w.commit()
    blocked = rs.search_routes(w, "Hub A", "Hub Z", avoid_hubs=["Hub M"])
    assert blocked["routes"] == [] and blocked["no_route_reason"]["code"] == "NO_ROUTE_WITHIN_CONSTRAINTS"
    assert {"type": "remove_avoided_hub", "message": "A route exists if M is not avoided.", "value": "M"} in \
        blocked["no_route_reason"]["suggestions"]
    modes = rs.search_routes(w, "Hub A", "Hub Z", avoid_modes=["local_bus"])
    assert any(s["type"] == "remove_avoided_mode" for s in modes["no_route_reason"]["suggestions"])


def test_disconnected_hubs(w):
    r = search(w, "A", "Z")
    assert r["routes"] == [] and r["no_route_reason"]["code"] == "NO_ROUTE_FOUND"


# ---- alerts ---------------------------------------------------------------------------------
def test_verified_closed_alert_affects_routing(w):
    base = search(w, optimize="cheapest")["routes"][0]
    assert ids(base) == ["BUS_A_B", "BUS_B_C"]
    report(w, "verified", route_ids=("BUS_A_B",), closed=True)
    r = search(w, optimize="cheapest")
    assert ids(r["routes"][0]) != ids(base) and all("BUS_A_B" not in ids(o) for o in r["routes"])
    assert r["routes"][0]["total_regular_fare_min_bdt"] == 25
    assert r["disruptions"]["closed_routes"][0]["route_id"] == "BUS_A_B"


@pytest.mark.parametrize("status", [s.value for s in ReportStatus if s.value != "verified"])
def test_non_verified_alerts_do_not_affect_routing(w, status):
    report(w, status, route_ids=("BUS_A_B", "RICK_A_B"), closed=True, extra=30)
    r = search(w, optimize="cheapest")
    assert ids(r["routes"][0]) == ["BUS_A_B", "BUS_B_C"]
    assert r["disruptions"]["closed_routes"] == [] and r["disruptions"]["delayed_routes"] == []


def test_expired_verified_alert_is_ignored(w):
    report(w, "verified", closed=True, minutes=-5)
    assert ids(search(w, optimize="cheapest")["routes"][0]) == ["BUS_A_B", "BUS_B_C"]


def test_verified_delay_alert_adds_time(w):
    report(w, "verified", route_ids=("RICK_A_B",), extra=30)
    r = search(w, optimize="fastest")
    best = r["routes"][0]
    assert "RICK_A_B" not in ids(best)  # 5 + 30 min makes it slower than A-D-C (24) and A-C (20)
    assert best["total_duration_min"] == 20 and ids(best) == ["RICK_A_C"]
    delayed = rs.build_graph(w).adj["A"]
    assert next(e for e in delayed if e.route_id == "RICK_A_B").time_min == 35
    assert w.get(RouteEdge, "RICK_A_B").time_normal_min == 5  # source row untouched


def test_admin_closed_edge_is_excluded(w):
    w.get(RouteEdge, "BUS_A_B").status = "closed"
    w.commit()
    graph = rs.build_graph(w)
    assert "BUS_A_B" in graph.closed and graph.closed["BUS_A_B"].startswith("admin")
    assert all(e.route_id != "BUS_A_B" for e in graph.edges)


# ---- simulation ------------------------------------------------------------------------------
def sim(session, session_id="S1", effects=None, scenario_type="blocked_intersection", zone="B", status="active", sid="sim1"):
    session.add(Simulation(scenario_id=sid, session_id=session_id, scenario_type=scenario_type, zone_hub=zone,
                           status=status, effects_json=effects if effects is not None else {"close_zone_edges": True}))
    session.commit()


def test_session_simulation_affects_only_that_session(w):
    sim(w, "S1")
    plain = search(w, optimize="cheapest")
    mine = search(w, optimize="cheapest", session_id="S1")
    other = search(w, optimize="cheapest", session_id="S2")
    assert ids(mine["routes"][0]) == ["BUS_A_C"] and mine["disruptions"]["simulation_scenario_id"] == "sim1"
    assert all("B" not in (l["from_hub"], l["to_hub"]) for o in mine["routes"] for l in o["legs"])
    assert ids(plain["routes"][0]) == ids(other["routes"][0]) == ["BUS_A_B", "BUS_B_C"]
    assert other["disruptions"]["simulation_scenario_id"] is None


def test_simulation_never_mutates_route_edges(w):
    before = [(e.route_id, e.status, e.time_normal_min, e.closed_reason) for e in w.exec(select(RouteEdge)).all()]
    sim(w, "S1", {"close_zone_edges": True, "time_multiplier": 3.0, "added_time_min": 5.0})
    search(w, session_id="S1")
    w.expire_all()
    assert before == [(e.route_id, e.status, e.time_normal_min, e.closed_reason) for e in w.exec(select(RouteEdge)).all()]


def test_reset_or_expired_simulation_is_ignored(w):
    sim(w, "S1", status="reset", sid="s_reset")
    sim(w, "S1", sid="s_old")
    w.get(Simulation, "s_old").expires_at = utcnow() - timedelta(minutes=1)
    w.commit()
    r = search(w, optimize="cheapest", session_id="S1")
    assert r["disruptions"]["simulation_scenario_id"] is None and ids(r["routes"][0]) == ["BUS_A_B", "BUS_B_C"]


def test_simulation_time_multiplier_and_added_time(w):
    sim(w, "S1", {"time_multiplier": 2.0, "added_time_min": 3.0}, zone=None, scenario_type="peak_rush_hour")
    edge = next(e for e in rs.build_graph(w, "S1").adj["A"] if e.route_id == "RICK_A_B")
    assert edge.time_min == 5 * 2 + 3 and edge.sim_added_time_min == 3
    assert next(e for e in rs.build_graph(w, "S2").adj["A"] if e.route_id == "RICK_A_B").time_min == 5


def test_simulation_fare_scenario_and_default_effects(w):
    sim(w, "S1", {}, zone=None, scenario_type="heavy_rain")  # empty effects -> dataset-backed defaults
    graph = rs.build_graph(w, "S1")
    assert graph.scenario == "rain" and graph.time_scenario == "rain"
    r = search(w, optimize="cheapest", session_id="S1")
    assert r["scenario"] == "rain" and r["routes"][0]["total_regular_fare_min_bdt"] == 40  # explicit rain columns = 2x
    assert search(w, optimize="cheapest")["scenario"] == "normal"


def test_requested_scenario_overrides_simulation_scenario_with_warning(w):
    sim(w, "S1", {}, zone=None, scenario_type="heavy_rain")
    r = search(w, optimize="cheapest", session_id="S1", scenario="detour")
    assert r["scenario"] == "detour" and any("SCENARIO_CONFLICT" in x for x in r["warnings"])


def test_simulation_flood_closure_warns_when_dataset_flags_nothing(w):
    sim(w, "S1", {"close_flood_prone": True}, zone=None)
    r = search(w, session_id="S1")
    assert any("SIMULATION_NO_EFFECT" in x for x in r["warnings"])
    w.get(RouteEdge, "BUS_A_B").flood_prone = True
    w.commit()
    assert "BUS_A_B" in rs.build_graph(w, "S1").closed


def test_simulation_transfer_wait_only_when_configured(w):
    plain = search(w, optimize="fastest")["routes"][0]
    assert plain["total_transfer_wait_min"] == 0 and plain["transfers"] == 1
    sim(w, "S1", {"transfer_wait_min": 4}, zone=None)
    waited = search(w, optimize="fastest", session_id="S1")["routes"][0]
    assert waited["transfers"] == 1 and waited["total_transfer_wait_min"] == 4
    assert waited["total_duration_min"] == waited["total_travel_time_min"] + 4


# ---- direction ---------------------------------------------------------------------------------
def test_one_way_edge_only_traversed_forward(w):
    w.get(RouteEdge, "BUS_C_X").direction = "one_way"
    w.commit()
    graph = rs.build_graph(w)
    assert any(e.to_hub == "X" for e in graph.adj["C"]) and "X" not in graph.adj


def test_unknown_direction_is_forward_only_unless_source_is_symmetric(w, monkeypatch):
    monkeypatch.setattr(rs, "ASSUME_REVERSE_UNKNOWN", False)
    w.get(RouteEdge, "BUS_C_X").direction = "unknown"
    w.get(RouteEdge, "BUS_C_X").source_dataset = "co-ordination"
    w.get(RouteEdge, "RICK_A_C").direction = "unknown"
    w.get(RouteEdge, "RICK_A_C").source_dataset = "dhaka_transport_fares"
    w.commit()
    graph = rs.build_graph(w)
    assert "X" not in graph.adj  # forward only
    forward = next(e for e in graph.adj["C"] if e.route_id == "BUS_C_X")
    assert any("DIRECTION_UNKNOWN" in x for x in forward.warnings)
    assert any(e.route_id == "RICK_A_C" for e in graph.adj["C"])  # symmetric source: both directions
    assert not any("DIRECTION_UNKNOWN" in x for e in graph.adj["C"] if e.route_id == "RICK_A_C" for x in e.warnings)


def test_unknown_direction_assumed_two_way_when_configured(w, monkeypatch):
    monkeypatch.setattr(rs, "ASSUME_REVERSE_UNKNOWN", True)
    w.get(RouteEdge, "BUS_C_X").direction = "unknown"
    w.get(RouteEdge, "BUS_C_X").source_dataset = "co-ordination"
    w.commit()
    graph = rs.build_graph(w)
    back = next(e for e in graph.adj["X"] if e.route_id == "BUS_C_X")
    assert any("DIRECTION_ASSUMED" in x for x in back.warnings)


# ---- route options -------------------------------------------------------------------------------
def test_route_option_returns_fare_ranges(w):
    for o in search(w)["routes"]:
        assert o["total_regular_fare_min_bdt"] <= o["total_regular_fare_max_bdt"]
        rick = [l for l in o["legs"] if l["mode"] == "rickshaw"]
        for l in rick:
            assert l["regular_fare_min_bdt"] < l["regular_fare_max_bdt"]  # a range, not one number
    r = by_label(search(w), "fewest_transfers")
    assert (r["total_regular_fare_min_bdt"], r["total_regular_fare_max_bdt"]) == (100, 150)


def test_student_range_equals_regular_without_discount(w):
    r = by_label(search(w, prefer_student_fare=True), "fewest_transfers")  # all rickshaw
    assert ids(r) == ["RICK_A_C"]
    assert r["total_student_fare_min_bdt"] == r["total_regular_fare_min_bdt"] == 100
    assert r["total_student_fare_max_bdt"] == r["total_regular_fare_max_bdt"] == 150
    assert r["legs"][0]["student_discount_applies"] is False


def test_student_fare_used_for_ranking_and_regular_still_returned(w):
    regular = search(w, optimize="cheapest")["routes"][0]
    assert ids(regular) == ["BUS_A_B", "BUS_B_C"]  # 10 + 10 beats the direct 25
    r = search(w, optimize="cheapest", prefer_student_fare=True)["routes"][0]
    # student: direct bus 25 -> 13, while each 10 BDT leg stays at the 10 BDT floor (total 20)
    assert ids(r) == ["BUS_A_C"]
    assert (r["total_regular_fare_min_bdt"], r["total_student_fare_min_bdt"]) == (25, 13)
    assert r["legs"][0]["student_discount_applies"] is True


def test_student_preference_changes_cheapest_choice(w):
    w.add(RouteEdge(route_id="RICK_A_X", mode="rickshaw", origin_hub="A", destination_hub="X", direction="bidirectional",
                    time_normal_min=5))
    w.add(RouteEdge(route_id="BUS_A_X", mode="local_bus", origin_hub="A", destination_hub="X", direction="bidirectional",
                    time_normal_min=5))
    w.add(FareMatrixRow(mode="rickshaw", from_hub="A", to_hub="X", normal_low_bdt=20, normal_high_bdt=25))
    w.add(FareMatrixRow(mode="local_bus", from_hub="A", to_hub="X", normal_low_bdt=30, normal_high_bdt=30,
                        student_fare_low_bdt=15, student_fare_high_bdt=15))
    w.commit()
    assert ids(rs.search_routes(w, "Hub A", "Hub X", optimize="cheapest")["routes"][0]) == ["RICK_A_X"]
    assert ids(rs.search_routes(w, "Hub A", "Hub X", optimize="cheapest", prefer_student_fare=True)["routes"][0]) == ["BUS_A_X"]


def test_duration_sums_correctly(w):
    sim(w, "S1", {"transfer_wait_min": 3}, zone=None)
    for o in search(w, session_id="S1")["routes"]:
        legs = o["legs"]
        travel = sum(l["travel_time_min"] for l in legs)
        wait = sum(l["transfer_wait_after_min"] for l in legs)
        assert o["total_travel_time_min"] == travel and o["total_transfer_wait_min"] == wait
        assert o["total_duration_min"] == travel + wait + 0  # walking is already inside travel time
        assert o["total_walking_time_min"] == 0
        assert o["leg_count"] == len(legs) and o["transfers"] == (3 and wait / 3)


def test_walk_legs_count_in_travel_and_walking_not_as_transfers(w):
    w.add(Hub(hub_id="W", name="Hub W", latitude=23.74, longitude=90.401))
    w.add(RouteEdge(route_id="WALK_C_W", mode="walk", origin_hub="C", destination_hub="W", direction="bidirectional",
                    time_normal_min=7))
    w.add(RouteEdge(route_id="BUS_W_Z", mode="local_bus", origin_hub="W", destination_hub="Z", direction="bidirectional",
                    time_normal_min=10))
    w.add(FareMatrixRow(mode="local_bus", from_hub="W", to_hub="Z", normal_low_bdt=5, normal_high_bdt=5))
    w.add(FareMatrixRow(mode="local_bus", from_hub="X", to_hub="W", normal_low_bdt=5, normal_high_bdt=5))
    w.commit()
    r = rs.search_routes(w, "Hub B", "Hub Z", optimize="cheapest", avoid_modes=["rickshaw", "cng"])
    best = r["routes"][0]
    assert ids(best) == ["BUS_B_C", "WALK_C_W", "BUS_W_Z"]
    assert best["total_walking_time_min"] == 7 and best["transfers"] == 0 and best["leg_count"] == 3
    assert best["total_travel_time_min"] == 15 + 7 + 10 and best["total_duration_min"] == 32
    walk = best["legs"][1]
    assert walk["travel_time_min"] == walk["walking_time_min"] == 7
    assert (walk["regular_fare_min_bdt"], walk["regular_fare_max_bdt"]) == (0, 0)


def test_transfers_count_mode_changes(w):
    fastest = by_label(search(w, optimize="fastest"), "fastest")
    assert fastest["transfers"] == 1 and fastest["leg_count"] == 2
    assert [l["mode"] for l in fastest["legs"]] == ["rickshaw", "cng"]


# ---- uniqueness & grounding ------------------------------------------------------------------------------
def test_at_least_three_unique_routes_when_graph_supports_it(w):
    r = search(w)
    seqs = [tuple(ids(o)) for o in r["routes"]]
    assert len(r["routes"]) >= 3 and len(set(seqs)) == len(seqs)
    assert "LESS_THAN_THREE_ROUTES_AVAILABLE" not in r["warning_codes"]


def test_alternates_fill_in_when_objectives_agree(w):
    # only one best path per objective can be the same; alternates must be found with penalties
    r = search(w, "A", "D", optimize="cheapest")
    seqs = [tuple(ids(o)) for o in r["routes"]]
    assert len(set(seqs)) == len(seqs) and len(seqs) >= 3
    assert any(o["rank_label"] == "alternative" for o in r["routes"])


def test_fewer_than_three_routes_gives_warning(w):
    r = search(w, "C", "X")  # only the one bus edge reaches X
    assert len(r["routes"]) == 1
    assert "LESS_THAN_THREE_ROUTES_AVAILABLE" in r["warning_codes"]


def test_no_invented_edges(w):
    valid = {(e.route_id, frozenset((e.origin_hub, e.destination_hub))) for e in w.exec(select(RouteEdge)).all()}
    for optimize in rs.OBJECTIVES:
        for o in search(w, optimize=optimize)["routes"]:
            assert o["grounding"] == {"valid": True, "checked_route_ids": True, "checked_hub_ids": True, "checked_fares": True}
            assert o["legs"][0]["from_hub"] == "A" and o["legs"][-1]["to_hub"] == "C"
            for a, b in zip(o["legs"], o["legs"][1:]):
                assert a["to_hub"] == b["from_hub"]
            for l in o["legs"]:
                assert (l["route_id"], frozenset((l["from_hub"], l["to_hub"]))) in valid


def test_no_edges_between_unlinked_hubs_means_no_route(w):
    n_edges = len(w.exec(select(RouteEdge)).all())
    assert search(w, "D", "Z")["routes"] == []
    assert len(w.exec(select(RouteEdge)).all()) == n_edges


def test_option_shape_and_ids(w):
    r = search(w)
    o = r["routes"][0]
    assert o["route_option_id"] == f"OPT_{o['rank_label'].upper()}_001"
    assert set(o) >= {"rank_label", "total_regular_fare_min_bdt", "total_regular_fare_max_bdt",
                      "total_student_fare_min_bdt", "total_student_fare_max_bdt", "total_travel_time_min",
                      "total_transfer_wait_min", "total_walking_time_min", "total_duration_min", "transfers",
                      "leg_count", "within_budget", "disruption_risk", "scenario", "legs", "warnings", "grounding"}
    assert set(o["legs"][0]) >= {"order", "mode", "route_id", "from_hub", "to_hub", "regular_fare_min_bdt",
                                 "student_fare_max_bdt", "student_discount_applies", "travel_time_min",
                                 "transfer_wait_after_min", "walking_time_min", "boarding_instruction",
                                 "alighting_instruction", "transfer_instruction", "warnings"}
    assert o["disruption_risk"] in ("low", "medium", "high") and o["legs"][0]["order"] == 1
    assert any("estimates" in x for x in o["warnings"])
    assert r["routes"][0]["rank_label"] == "balanced"  # the requested objective comes first


def test_disruption_risk_levels(w):
    assert search(w)["routes"][0]["disruption_risk"] == "low"
    report(w, "verified", route_ids=("RICK_A_B",), extra=1)
    fast = search(w, optimize="fastest")["routes"][0]
    assert ids(fast) == ["RICK_A_B", "CNG_B_C"] and fast["total_duration_min"] == 11  # still fastest, but delayed
    assert fast["disruption_risk"] == "medium"


def test_metro_ride_is_priced_once_from_the_official_matrix(w):
    for hub in ("M1", "M2", "M3"):
        w.add(Hub(hub_id=hub, name=f"Hub {hub}", latitude=23.9, longitude=90.1))
    for a, b in (("M1", "M2"), ("M2", "M3")):
        w.add(RouteEdge(route_id=f"MRT_{a}_{b}", mode="mrt", origin_hub=a, destination_hub=b, direction="bidirectional",
                        time_normal_min=4))
    w.add(MetroFareMatrix(from_station_hub_id="M1", to_station_hub_id="M3", fare_bdt=40))
    w.add(MetroFareMatrix(from_station_hub_id="M1", to_station_hub_id="M2", fare_bdt=30))
    w.add(MetroFareMatrix(from_station_hub_id="M2", to_station_hub_id="M3", fare_bdt=30))
    w.commit()
    r = rs.search_routes(w, "Hub M1", "Hub M3", optimize="fastest")
    o = r["routes"][0]
    assert ids(o) == ["MRT_M1_M2", "MRT_M2_M3"] and o["transfers"] == 0
    assert (o["total_regular_fare_min_bdt"], o["total_regular_fare_max_bdt"]) == (40, 40)  # not 30 + 30
    assert any(x.startswith("METRO_RIDE") for x in o["legs"][1]["warnings"])


def test_unpriced_leg_gives_null_totals_not_a_guess(w):
    w.add(Hub(hub_id="Q", name="Hub Q", latitude=23.9, longitude=90.1))
    w.add(RouteEdge(route_id="MRT_C_Q", mode="mrt", origin_hub="C", destination_hub="Q", direction="bidirectional",
                    time_normal_min=3))
    w.commit()
    o = rs.search_routes(w, "Hub C", "Hub Q", optimize="fastest")["routes"][0]
    assert o["total_regular_fare_min_bdt"] is None and o["legs"][0]["regular_fare_min_bdt"] is None
    assert o["grounding"]["checked_fares"] is False and o["grounding"]["valid"] is False
    assert any(x.startswith("FARE_UNKNOWN") for x in o["warnings"])
    assert rs.search_routes(w, "Hub C", "Hub Q", optimize="cheapest", budget_bdt=10)["routes"][0]["within_budget"] is False


# ---- hub resolution ------------------------------------------------------------------------------------
def test_resolve_hub_by_id_name_alias(w):
    assert rs.resolve_hub(w, "A").hub.hub_id == "A" and rs.resolve_hub(w, "A").matched_by == "hub_id"
    assert rs.resolve_hub(w, "hub b").matched_by == "name" and rs.resolve_hub(w, "HUB B").hub.hub_id == "B"
    assert rs.resolve_hub(w, "Alias C").matched_by == "alias" and rs.resolve_hub(w, "alias c").hub.hub_id == "C"


def test_resolve_hub_fuzzy_ambiguous_and_unknown(w):
    typo = rs.resolve_hub(w, "Hub Dd")
    assert typo.hub.hub_id == "D" and typo.matched_by == "fuzzy"
    w.add(Hub(hub_id="MIRPUR_10", name="Mirpur 10", aliases_json=["Mirpur"]))
    w.add(Hub(hub_id="MIRPUR_11", name="Mirpur 11"))
    w.add(Hub(hub_id="MIRPUR_12", name="Mirpur 12"))
    w.commit()
    assert rs.resolve_hub(w, "Mirpur").hub.hub_id == "MIRPUR_10"  # exact alias beats fuzzy
    amb = rs.resolve_hub(w, "Mirpu")
    assert amb.code == "AMBIGUOUS_HUB" and {c["hub_id"] for c in amb.candidates} >= {"MIRPUR_10", "MIRPUR_11"}
    unknown = rs.resolve_hub(w, "Narnia")
    assert unknown.code == "UNKNOWN_HUB" and unknown.hub is None


def test_search_reports_resolution_errors(w):
    unknown = rs.search_routes(w, "Narnia", "Hub C")
    assert unknown["routes"] == [] and unknown["error"]["code"] == "UNKNOWN_HUB" and unknown["error"]["field"] == "origin"
    w.add(Hub(hub_id="MIRPUR_10", name="Mirpur 10"))
    w.add(Hub(hub_id="MIRPUR_11", name="Mirpur 11"))
    w.commit()
    amb = rs.search_routes(w, "Hub A", "Mirpu")
    assert amb["error"]["code"] == "AMBIGUOUS_HUB" and amb["error"]["candidates"] and amb["error"]["field"] == "destination"
    assert rs.search_routes(w, "Hub A", "Alias A")["error"]["code"] == "SAME_ORIGIN_DESTINATION"
    assert rs.search_routes(w, "Hub A", "Hub C", optimize="teleport")["error"]["code"] == "INVALID_OPTIMIZE"
    assert rs.search_routes(w, "Hub A", "Hub C", scenario="snow")["error"]["code"] == "SCENARIO_UNKNOWN"


def test_search_works_without_any_data(session):
    r = rs.search_routes(session, "Anywhere", "Elsewhere")
    assert r["routes"] == [] and r["error"]["code"] == "UNKNOWN_HUB"
