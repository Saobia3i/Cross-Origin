"""Transit browsing endpoints on a fixture-seeded database (3 hubs, 3 edges; see tests/fixtures/mini_datasets).

Fixture facts: UTTARA_NORTH has 2 routes (an open metro edge, a bus edge the dataset marks closed);
FARMGATE the same; MIRPUR_10 has the 2 open metro edges. Report rpt_TEST_FIXTURE_001 is PENDING at
MIRPUR_10, rpt_TEST_FIXTURE_002 is VERIFIED (all-clear, no effect) at FARMGATE.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.enums import ReportStatus
from app.models import Report, Simulation, utcnow

MRT_U_M = "MRT6_UTTARA_NORTH_MIRPUR_10"
MRT_M_F = "MRT6_MIRPUR_10_FARMGATE"
BUS_F_U = "LOCAL_BUS_FARMGATE_UTTARA_NORTH"


def add(engine, *objs):
    with Session(engine) as s:
        for o in objs:
            s.add(o)
        s.commit()


def report(engine, rid="alert1", status="verified", routes=(MRT_U_M,), closed=False, extra=0, hub=None, minutes=60):
    add(engine, Report(report_id=rid, report_type="waterlogging", severity="heavy", description="d", reported_by=2,
                       half_life_min=60, expires_at=utcnow() + timedelta(minutes=minutes), status=status,
                       affected_route_ids_json=list(routes), edge_closed=closed, extra_time_min=extra,
                       affected_hub=hub))


def hubs(client, **params):
    r = client.get("/api/transit/hubs", params=params)
    assert r.status_code == 200
    return {h["hub_id"]: h for h in r.json()["data"]}


def assert_envelope(body, ok=True):
    assert set(body) == {"ok", "data", "meta", "errors"} and body["ok"] is ok
    assert body["meta"]["source"] == "dhakapath-backend" and body["meta"]["request_id"].startswith("req_")
    assert (body["errors"] == []) is ok


# ---- network ----------------------------------------------------------------------
def test_network_returns_envelope_and_all_sections(client):
    r = client.get("/api/transit/network")
    assert r.status_code == 200
    body = r.json()
    assert_envelope(body)
    d = body["data"]
    assert set(d) == {"hubs", "routes", "fare_rules", "mode_rates", "scenario_multipliers"}
    assert (len(d["hubs"]), len(d["routes"]), len(d["fare_rules"]), len(d["mode_rates"]),
            len(d["scenario_multipliers"])) == (3, 3, 12, 2, 14)
    bus_rule = next(x for x in d["fare_rules"] if x["mode"] == "local_bus")
    assert (bus_rule["student_discount_percent"], bus_rule["minimum_student_fare_bdt"]) == (50, 10)
    rick = next(x for x in d["mode_rates"] if x["mode"] == "rickshaw")
    assert rick["per_km_bdt"] == 22
    null_mult = [m for m in d["scenario_multipliers"] if m["multiplier"] is None]
    assert null_mult and all(m["scenario"] == "peak_plus_rain" for m in null_mult)  # dataset states none
    assert all("status" in h for h in d["hubs"]) and all("geometry" in x for x in d["routes"])


def test_network_geometry_only_on_request(client):
    plain = client.get("/api/transit/network").json()["data"]["routes"]
    full = client.get("/api/transit/network", params={"include_geometry": "true"}).json()["data"]["routes"]
    assert all(x["geometry"] is None for x in plain)
    assert {x["route_id"] for x in full if x["geometry"]} == {MRT_U_M, MRT_M_F}
    assert next(x for x in full if x["route_id"] == MRT_U_M)["geometry"]["type"] == "LineString"


def test_network_on_empty_database_is_still_an_envelope(engine):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override
    try:
        body = TestClient(app).get("/api/transit/network").json()
    finally:
        app.dependency_overrides.clear()
    assert_envelope(body)
    assert body["data"] == {"hubs": [], "routes": [], "fare_rules": [], "mode_rates": [], "scenario_multipliers": []}


# ---- hubs / derived status ----------------------------------------------------------------
def test_hubs_shape(client):
    r = client.get("/api/transit/hubs")
    assert_envelope(r.json())
    h = r.json()["data"][0]
    assert set(h) == {"hub_id", "name", "name_bn", "aliases", "latitude", "longitude", "type", "status",
                      "status_reason", "active_alert_count"}
    by = hubs(client)
    assert sorted(by) == ["FARMGATE", "MIRPUR_10", "UTTARA_NORTH"]
    assert by["MIRPUR_10"]["aliases"] == ["Mirpur"] and by["UTTARA_NORTH"]["type"] == "metro_station"
    assert by["MIRPUR_10"]["name_bn"] is None  # not invented


def test_hub_status_is_derived_from_routes_and_alerts(client):
    by = hubs(client)
    assert by["MIRPUR_10"]["status"] == "normal" and by["MIRPUR_10"]["status_reason"] is None
    # the dataset closes the bus edge: 1 of the 2 routes at these hubs
    for hub in ("FARMGATE", "UTTARA_NORTH"):
        assert by[hub]["status"] == "delayed" and "1 of 2 routes closed (admin 1)" in by[hub]["status_reason"]
    assert by["FARMGATE"]["active_alert_count"] == 1  # verified fixture report
    assert by["MIRPUR_10"]["active_alert_count"] == 0  # its fixture report is only pending


def test_verified_alert_closing_every_route_blocks_the_hub(client, engine):
    report(engine, routes=(MRT_U_M,), closed=True)
    by = hubs(client)
    assert by["UTTARA_NORTH"]["status"] == "blocked" and "all 2 routes" in by["UTTARA_NORTH"]["status_reason"]
    assert "verified alert 1" in by["UTTARA_NORTH"]["status_reason"] and by["UTTARA_NORTH"]["active_alert_count"] == 1
    assert by["MIRPUR_10"]["status"] == "delayed"  # 1 of its 2 routes closed
    assert by["FARMGATE"]["status"] == "delayed"


def test_verified_delay_alert_makes_hubs_delayed(client, engine):
    report(engine, routes=(MRT_M_F,), extra=15)
    by = hubs(client)
    assert by["MIRPUR_10"]["status"] == "delayed" and "alert1" in by["MIRPUR_10"]["status_reason"]
    assert by["UTTARA_NORTH"]["status_reason"].count("delay") == 0  # only its own closure note
    summary = {s["hub_id"]: s for s in client.get("/api/transit/hub-statuses").json()["data"]["hub_statuses"]}
    assert summary["FARMGATE"]["active_alert_ids"] == ["alert1", "rpt_TEST_FIXTURE_002"] or \
        set(summary["FARMGATE"]["active_alert_ids"]) == {"alert1", "rpt_TEST_FIXTURE_002"}


def test_alert_with_affected_hub_and_delay(client, engine):
    report(engine, routes=(), extra=10, hub="UTTARA_NORTH")
    by = hubs(client)
    assert "verified alert(s) add delay" in by["UTTARA_NORTH"]["status_reason"]
    assert by["MIRPUR_10"]["status"] == "normal"


@pytest.mark.parametrize("status", [s.value for s in ReportStatus if s.value != "verified"])
def test_non_verified_alerts_do_not_change_status(client, engine, status):
    report(engine, status=status, routes=(MRT_U_M, MRT_M_F), closed=True, extra=30)
    by = hubs(client)
    assert by["MIRPUR_10"]["status"] == "normal" and by["MIRPUR_10"]["active_alert_count"] == 0


def test_expired_verified_alert_is_ignored(client, engine):
    report(engine, routes=(MRT_U_M, MRT_M_F), closed=True, minutes=-1)
    assert hubs(client)["MIRPUR_10"]["status"] == "normal"


def test_simulation_status_is_per_session(client, engine):
    add(engine, Simulation(scenario_id="sim1", session_id="S1", scenario_type="blocked_intersection",
                           zone_hub="MIRPUR_10", effects_json={"close_zone_edges": True}))
    mine, other, nobody = hubs(client, session_id="S1"), hubs(client, session_id="S2"), hubs(client)
    assert mine["MIRPUR_10"]["status"] == "blocked" and "simulation" in mine["MIRPUR_10"]["status_reason"]
    # the zone closes both metro edges and the dataset already closed the bus edge: nothing left anywhere
    assert {h["status"] for h in mine.values()} == {"blocked"}
    assert other["MIRPUR_10"]["status"] == nobody["MIRPUR_10"]["status"] == "normal"
    statuses = client.get("/api/transit/hub-statuses", params={"session_id": "S1"}).json()["data"]["hub_statuses"]
    assert next(s for s in statuses if s["hub_id"] == "MIRPUR_10")["status"] == "blocked"


def test_simulation_delay_marks_zone_hub_delayed(client, engine):
    add(engine, Simulation(scenario_id="sim2", session_id="S1", scenario_type="peak_rush_hour", zone_hub="MIRPUR_10",
                           effects_json={"added_time_min": 5}))
    assert hubs(client, session_id="S1")["MIRPUR_10"]["status"] == "delayed"
    assert hubs(client)["MIRPUR_10"]["status"] == "normal"


def test_hub_statuses_shape(client):
    r = client.get("/api/transit/hub-statuses")
    assert_envelope(r.json())
    items = r.json()["data"]["hub_statuses"]
    assert len(items) == 3 and all(set(i) == {"hub_id", "status", "reason", "active_alert_ids"} for i in items)
    assert {i["status"] for i in items} <= {"normal", "delayed", "blocked"}


# ---- routes ------------------------------------------------------------------------------------
def test_routes_shape_and_disruption_info(client):
    r = client.get("/api/transit/routes")
    assert_envelope(r.json())
    routes = {x["route_id"]: x for x in r.json()["data"]}
    assert set(routes) == {MRT_U_M, MRT_M_F, BUS_F_U}
    assert set(routes[MRT_U_M]) >= {"route_id", "mode", "origin_hub", "destination_hub", "direction", "status",
                                    "closed_reason", "road_distance_km", "time_normal_min", "boarding_notes",
                                    "alighting_notes", "student_eligible"}
    bus = routes[BUS_F_U]
    assert (bus["status"], bus["closed_by"], bus["direction"], bus["student_eligible"]) == ("closed", "admin", "one_way", True)
    assert bus["closed_reason"].startswith("admin") and bus["road_distance_km"] == 9.99
    assert routes[MRT_U_M]["status"] == "active" and routes[MRT_U_M]["closed_reason"] is None
    assert routes[MRT_U_M]["direction"] == "unknown" and routes[MRT_U_M]["boarding_notes"] == []


def test_routes_reflect_verified_alerts_and_sessions(client, engine):
    report(engine, routes=(MRT_U_M,), closed=True)
    report(engine, rid="alert2", routes=(MRT_M_F,), extra=12)
    add(engine, Simulation(scenario_id="s", session_id="S1", scenario_type="blocked_intersection", zone_hub="FARMGATE",
                           effects_json={"close_zone_edges": True}))
    routes = {x["route_id"]: x for x in client.get("/api/transit/routes").json()["data"]}
    assert routes[MRT_U_M]["status"] == "closed" and routes[MRT_U_M]["closed_by"] == "verified_alert"
    assert routes[MRT_U_M]["active_alert_ids"] == ["alert1"]
    assert routes[MRT_M_F]["status"] == "active" and routes[MRT_M_F]["extra_time_min"] == 12
    mine = {x["route_id"]: x for x in client.get("/api/transit/routes", params={"session_id": "S1"}).json()["data"]}
    assert mine[MRT_M_F]["status"] == "closed" and mine[MRT_M_F]["closed_by"] == "simulation"
    assert routes[MRT_M_F]["status"] == "active"  # other sessions are unaffected


def test_routes_filters(client):
    assert {x["route_id"] for x in client.get("/api/transit/routes", params={"mode": "mrt"}).json()["data"]} == {MRT_U_M, MRT_M_F}
    assert [x["route_id"] for x in client.get("/api/transit/routes", params={"status": "closed"}).json()["data"]] == [BUS_F_U]
    assert {x["route_id"] for x in client.get("/api/transit/routes", params={"hub_id": "UTTARA_NORTH"}).json()["data"]} == {MRT_U_M, BUS_F_U}
    bad = client.get("/api/transit/routes", params={"mode": "hovercraft"})
    assert bad.status_code == 422
    assert_envelope(bad.json(), ok=False)
    assert bad.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_routes_do_not_mutate_the_database(client, engine):
    report(engine, routes=(MRT_U_M,), closed=True)
    client.get("/api/transit/routes")
    from app.models import RouteEdge
    with Session(engine) as s:
        assert s.get(RouteEdge, MRT_U_M).status == "active"


def test_catalog_marks_new_endpoints_implemented(client):
    eps = {(e["method"], e["path"]): e for e in client.get("/api/catalog").json()["data"]["endpoints"]}
    for key in (("GET", "/api/transit/network"), ("GET", "/api/transit/hubs"), ("GET", "/api/transit/routes"),
                ("GET", "/api/transit/hub-statuses"), ("POST", "/api/trips/search"), ("POST", "/api/fares/estimate")):
        assert eps[key]["implemented"] is True and eps[key]["auth"] == "public"
    assert eps[("POST", "/api/chat")]["implemented"] is False
    assert client.post("/api/chat", json={}).status_code == 501  # still a placeholder
