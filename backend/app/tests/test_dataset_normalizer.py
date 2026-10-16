import json

import pytest

from app.services import dataset_manifest as dm
from app.services import dataset_normalizer as dn
from app.services.dataset_loader import build_manifest


def run(manifest, tmp_path, **kw):
    return dn.normalize(manifest, tmp_path / "out", **kw)


@pytest.fixture
def fixture_report(tmp_path):
    return run(build_manifest("fixtures"), tmp_path)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def mini_roots(tmp_path, polyline_rows, mapping_layers=None):
    """Temporary dataset roots with one headerless co-ordination file."""
    coord = tmp_path / "src" / "co-ordination"
    write(coord / "Routemap-X.csv", "\n".join(polyline_rows) + "\n")
    layers = mapping_layers or {"L": {"role": "transit_route", "mode": "metro", "mode_code": "MRT6"}}
    write(coord / "coordination_mapping.json", json.dumps({"layers": layers}))
    return {"root": tmp_path / "src", "coordination": coord}


# --- end-to-end on fixtures ----------------------------------------------------
def test_fixture_outputs(fixture_report, tmp_path):
    out = tmp_path / "out"
    for name in list(dn.OUTPUT_FILES.values()) + [dn.REPORT_FILE]:
        assert (out / name).exists()
    o = fixture_report["_outputs"]
    assert len(o["hubs"]) == 3
    assert [e["route_id"] for e in o["route_edges"]] == [
        "MRT6_UTTARA_NORTH_MIRPUR_10", "MRT6_MIRPUR_10_FARMGATE", "LOCAL_BUS_FARMGATE_UTTARA_NORTH"]
    assert len(o["metro_fares"]) == 6
    assert len(o["mode_rates"]) == 2
    assert len(o["multipliers"]) == 2 * len(dn.SCENARIOS)
    assert [w["hub_id"] for w in o["wealth_indices"] if w["kind"] == "hub_wealth_index"] == ["MIRPUR_10"]
    assert [r["key"] for r in o["rules"]] == ["student"]
    assert fixture_report["contains_test_data"] is True
    assert all(h["is_test_fixture"] for h in o["hubs"])


def test_route_ids_are_deterministic(tmp_path):
    a = run(build_manifest("fixtures"), tmp_path / "a")["_outputs"]
    b = run(build_manifest("fixtures"), tmp_path / "b")["_outputs"]
    assert a["route_edges"] == b["route_edges"]
    assert a["hubs"] == b["hubs"]


def test_duplicate_edges_get_suffix_in_row_order(tmp_path):
    rows = ["L,0.001,0.001,0.002,0.002,A,B", "L,0.005,0.005,0.006,0.006,A,B", "L,0.009,0.009,0.008,0.008,A,B"]
    report = run(build_manifest("fixtures", mini_roots(tmp_path, rows)), tmp_path)
    edges = report["_outputs"]["route_edges"]
    assert [(e["route_id"], e["source_row"]) for e in edges] == [
        ("MRT6_A_B", 1), ("MRT6_A_B_2", 2), ("MRT6_A_B_3", 3)]


def test_fare_row_attaches_to_coordination_edge_via_alias(fixture_report):
    o = fixture_report["_outputs"]
    [fare] = o["fare_rows"]
    assert fare["to_hub_name"] == "Uttara" and fare["to_hub_id"] == "UTTARA_NORTH"
    assert fare["route_id"] == "LOCAL_BUS_FARMGATE_UTTARA_NORTH"
    assert fare["route_match"] == "same_direction"
    assert fare["fares"]["normal"] == {"low_bdt": 11.0, "high_bdt": 12.0}
    assert fare["fares"]["student"] == {"low_bdt": 10.0, "high_bdt": 10.0}
    edge = o["route_edges"][2]
    assert edge["fare_row_ids"] == ["FR_0001"]
    assert edge["status"] == "closed" and edge["one_way"] is True and edge["direction_unknown"] is False


def test_fare_rows_create_edges_when_coordination_has_none(tmp_path):
    src = tmp_path / "src"
    header = ",".join(dm.EXPECTED_FARE_COLUMNS)
    values = ["Rickshaw", "Non-app", "p", "Mirpur", "Farmgate", "5", "10", "20", "30", ""] + ["1"] * 22
    write(src / "Dhaka_Transport_Fares.csv", header + "\n" + ",".join(values) + "\n")
    report = run(build_manifest("fixtures", {"root": src}), tmp_path)
    [edge] = report["_outputs"]["route_edges"]
    assert edge["route_id"] == "RICKSHAW_MIRPUR_10_FARMGATE"
    assert edge["source_dataset"] == "dhaka_transport_fares"
    assert edge["direction_unknown"] is True and edge["geometry"] is None
    assert edge["distance_km"] == 5.0


# --- missing files ---------------------------------------------------------------
def test_contract_mode_when_everything_is_missing(tmp_path):
    roots = {"root": tmp_path / "empty", "coordination": tmp_path / "empty" / "co-ordination"}
    (tmp_path / "empty").mkdir()
    report = run(build_manifest("production", roots), tmp_path, problem_owner_fallback=False)
    assert report["contract_mode"] is True
    assert report["production_data_available"] is False
    assert "production data unavailable" in report["message"]
    for name in dn.OUTPUT_FILES.values():
        assert json.loads((tmp_path / "out" / name).read_text(encoding="utf-8")) == []
    assert all(d["status"] == "missing" for d in report["datasets"].values())


def test_missing_rules_file_falls_back_to_problem_owner_brief(tmp_path):
    (tmp_path / "empty").mkdir()
    report = run(build_manifest("production", {"root": tmp_path / "empty"}), tmp_path)
    assert report["contract_mode"] is True  # no dataset files at all
    o = report["_outputs"]
    assert len(o["mode_rates"]) == 9 and all(r["source"] == "problem_owner_brief" for r in o["mode_rates"])
    assert report["datasets"]["section_rules"]["sections"]["mode_rates"] == "fallback_problem_owner_brief"
    assert o["fare_rows"] == [] and o["route_edges"] == []


# --- metro matrix -------------------------------------------------------------
def test_metro_matrix_conversion(fixture_report):
    m = fixture_report["_outputs"]["metro_fares"]
    pairs = {(r["from_hub_id"], r["to_hub_id"]): r["fare_bdt"] for r in m}
    assert pairs == {
        ("UTTARA_NORTH", "MIRPUR_10"): 11, ("UTTARA_NORTH", "FARMGATE"): 22,
        ("MIRPUR_10", "UTTARA_NORTH"): 11, ("MIRPUR_10", "FARMGATE"): 33,
        ("FARMGATE", "UTTARA_NORTH"): 22, ("FARMGATE", "MIRPUR_10"): 33,
    }
    r = m[0]
    assert r["mode"] == "metro" and r["source"] == "Dhaka_Metro_Fare_Matrix" and r["official"] is True
    assert r["affected_by_weather"] is False and r["affected_by_blockage"] is False
    assert r["wealth_multiplier_applicable"] is False and r["student_discount_applicable"] is False


def test_metro_matrix_alias_mapping(tmp_path):
    src = tmp_path / "src"
    write(src / "Dhaka_Metro_Fare_Matrix.csv",
          "From/To,Uttara,Mirpur,Farmgate\nUttara,0,40,70\nMirpur,40,0,30\nFarmgate,70,30,0\n")
    m = run(build_manifest("fixtures", {"root": src}), tmp_path)["_outputs"]["metro_fares"]
    assert {r["from_hub_id"] for r in m} == {"UTTARA_NORTH", "MIRPUR_10", "FARMGATE"}


# --- rules parsing ----------------------------------------------------------------
def test_mode_rate_parsing_text_line():
    src = {"source": "t", "source_file": "t"}
    rows = [["Car (Taxi / hired car): base 80 + 50/km, min 200, 20 km/h, low/high band x0.85/1.3"]]
    [r] = dn._parse_mode_rates(rows, src)
    assert (r["mode"], r["base_fare_bdt"], r["per_km_bdt"], r["min_fare_bdt"], r["average_speed_kmh"],
            r["low_band_multiplier"], r["high_band_multiplier"]) == ("Car (Taxi / hired car)", 80, 50, 200, 20, 0.85, 1.3)


def test_rules_column_layout(tmp_path):
    src = tmp_path / "src"
    write(src / "Assumptions.csv",
          "Section,Item,Value,Meaning\n"
          "Hub wealth index,Mirpur,0.95,Lower-middle\n"
          'Mode rates,Local Bus,"base 0 + 2.45/km, min 10, 14 km/h",BDT; low/high band x1/1\n'
          'Multipliers,Local Bus,"rain x1.0, peak x1.0, detour x1.15, regular holiday x0.95, festive x1.1",Applied to normal fare\n'
          'Rule,Student,"50% off bus only, min Tk 10",\n'
          'Rule,Disclaimer,"Estimates only. Metro mapping: Uttara=Uttara North, Mirpur=Mirpur 10.",\n')
    o = run(build_manifest("fixtures", {"root": src}), tmp_path)["_outputs"]
    [rate] = o["mode_rates"]
    assert rate["mode_code"] == "LOCAL_BUS" and rate["per_km_bdt"] == 2.45 and rate["min_fare_bdt"] == 10
    assert rate["low_band_multiplier"] == 1 and rate["high_band_multiplier"] == 1
    [w] = o["wealth_indices"]
    assert w["hub_id"] == "MIRPUR_10" and w["wealth_index"] == 0.95 and w["note"] == "Lower-middle"
    rules = {r["key"]: r for r in o["rules"]}
    assert rules["student"]["params"] == {"discount_pct": 50, "applies_to_mode_codes": ["LOCAL_BUS"],
                                          "min_fare_bdt": 10.0}
    assert rules["disclaimer"]["params"]["declared_aliases"] == [
        {"alias": "Uttara", "canonical": "Uttara North"}, {"alias": "Mirpur", "canonical": "Mirpur 10"}]


def test_multiplier_parsing_covers_all_scenarios(fixture_report):
    mult = [m for m in fixture_report["_outputs"]["multipliers"] if m["mode_code"] == "RICKSHAW"]
    by = {m["scenario"]: m for m in mult}
    assert sorted(by) == sorted(dn.SCENARIOS)
    assert by["normal"]["multiplier"] == 1.0 and by["normal"]["basis"] == "implicit_baseline"
    assert by["rain"]["multiplier"] == 1.1 and by["festive_holiday"]["multiplier"] == 1.4
    assert by["peak_plus_rain"]["multiplier"] is None
    assert by["peak_plus_rain"]["basis"] == "not_specified_in_source"


def test_multiplier_text_line():
    [m] = dn._parse_multipliers([["Pathao Bike: rain x1.7, peak x1.5, detour x1.45, regular holiday x0.85, festive x1.4"]],
                                {"source": "t", "source_file": "t"})
    assert m["mode"] == "Pathao Bike"
    assert m["values"] == {"rain": 1.7, "peak": 1.5, "detour": 1.45, "regular_holiday": 0.85, "festive_holiday": 1.4}


def test_problem_owner_brief_parses_completely():
    sets = dn.parse_rules_grid(dn.problem_owner_grid(), None, {"source": "b", "source_file": "b"})
    assert len(sets["wealth"]) == 15 and len(sets["mode_rates"]) == 9
    assert len(sets["multipliers"]) == 9 and len(sets["rules"]) == 9
    rules = {r["key"]: r["params"] for r in sets["rules"]}
    assert rules["road_distance"] == {"straight_line_multiplier": 1.35}
    assert rules["peak_hours"]["windows"] == [{"start": "08:00", "end": "10:00"}, {"start": "17:00", "end": "20:00"}]
    assert rules["detour"]["extra_distance_pct"] == 35


# --- coordinates & geometry -------------------------------------------------------
def _input_points(report):
    pts = set()
    for e in report["_outputs"]["route_edges"]:
        if e["geometry"]:
            pts |= {tuple(c) for c in e["geometry"]["coordinates"]}
    return pts


def test_no_fabricated_coordinates(fixture_report):
    pts = _input_points(fixture_report)
    for h in fixture_report["_outputs"]["hubs"]:
        assert (h["longitude"], h["latitude"]) in pts
        assert h["coordinate_source"]["source_file"].endswith("_TEST_FIXTURE.csv")


def test_hubs_without_geometry_have_null_coordinates(tmp_path):
    src = tmp_path / "src"
    write(src / "Dhaka_Metro_Fare_Matrix.csv",
          "From/To,Uttara North,Mirpur 10,Farmgate\nUttara North,0,1,2\nMirpur 10,1,0,3\nFarmgate,2,3,0\n")
    hubs = run(build_manifest("fixtures", {"root": src}), tmp_path)["_outputs"]["hubs"]
    assert len(hubs) == 3
    assert all(h["latitude"] is None and h["longitude"] is None and h["coordinate_status"] == "missing" for h in hubs)


def test_orientation_and_label_conflicts(tmp_path):
    rows = [
        "L,0.001,0.001,0.002,0.002,A,B",   # as listed
        "L,0.003,0.003,0.002,0.002,B,C",   # geometry drawn C->B: reversed
        "L,0.003,0.003,0.004,0.004,X,D",   # shares C's point but calls it X: label conflict
    ]
    report = run(build_manifest("fixtures", mini_roots(tmp_path, rows)), tmp_path)
    edges = report["_outputs"]["route_edges"]
    assert [e["geometry_orientation"] for e in edges] == ["as_listed", "reversed", "as_listed"]
    assert edges[0]["geometry"]["coordinates"][0] == [0.001, 0.001]  # stored as provided
    assert report["label_conflicts"][0]["names"] == ["C", "X"]
    hubs = {h["hub_id"]: h for h in report["_outputs"]["hubs"]}
    assert (hubs["B"]["longitude"], hubs["B"]["latitude"]) == (0.002, 0.002)
    assert hubs["B"]["coordinate_source"]["basis"] == "junction_confirmed"
    assert hubs["C"]["latitude"] is None and hubs["X"]["latitude"] is None  # conflicting point not used
    assert (hubs["D"]["longitude"], hubs["D"]["latitude"]) == (0.004, 0.004)
    assert all(e["direction_unknown"] for e in edges)


def test_combined_stop_name_is_split_with_warning(tmp_path):
    rows = ["L,0.001,0.001,0.002,0.002,Farm gate. Karwan Bazar"]
    report = run(build_manifest("fixtures", mini_roots(tmp_path, rows)), tmp_path)
    [edge] = report["_outputs"]["route_edges"]
    assert (edge["origin_hub_id"], edge["destination_hub_id"]) == ("FARMGATE", "KARWAN_BAZAR")
    assert edge["warnings"] and any("split" in w for w in report["warnings"])


def test_road_network_rows(tmp_path):
    rows = ["Road,90.40,23.85,90.41,23.86,0,1.5"]
    layers = {"Road": {"role": "road_network", "trailing_numeric_fields": ["attr_flag_raw", "attr_cost_raw"]}}
    report = run(build_manifest("fixtures", mini_roots(tmp_path, rows, layers)), tmp_path)
    [seg] = report["_outputs"]["road_segments"]
    assert seg["coordinates"] == [[90.40, 23.85], [90.41, 23.86]]
    assert seg["attr_flag_raw"] == 0 and seg["attr_cost_raw"] == 1.5
    assert report["_outputs"]["route_edges"] == []


def test_production_run_skips_fixture_files(tmp_path):
    src = tmp_path / "src"
    write(src / "Dhaka_Metro_Fare_Matrix_TEST_FIXTURE.csv",
          "From/To,Uttara North,Mirpur 10,Farmgate\nUttara North,0,1,2\nMirpur 10,1,0,3\nFarmgate,2,3,0\n")
    report = run(build_manifest("production", {"root": src}), tmp_path)
    assert report["_outputs"]["metro_fares"] == []
    assert any("skipped test fixture" in w for w in report["warnings"])
