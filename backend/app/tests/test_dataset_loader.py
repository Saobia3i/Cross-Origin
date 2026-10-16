from pathlib import Path

import openpyxl
import pytest

from app.services import dataset_loader as dl
from app.services import dataset_manifest as dm

FIXTURES = dm.FIXTURE_ROOT


@pytest.mark.parametrize("raw,expected", [
    ("Road_Distance_km", "road_distance_km"),
    ("Peak_Office_Hour_Low_BDT", "peak_office_hour_low_bdt"),
    ("From / To Station (BDT)", "from_to_station_bdt"),
    ("fromHub", "from_hub"),
    ("Dhaka Univ.", "dhaka_univ"),
    ("  Time Normal (min) ", "time_normal_min"),
    ("", "column_1"),
])
def test_normalize_header(raw, expected):
    assert dl.normalize_header(raw) == expected


def test_normalize_headers_keeps_raw_mapping_and_dedupes():
    norm, mapping = dl.normalize_headers(["Mode", "From_Hub", "mode"])
    assert norm == ["mode", "from_hub", "mode_2"]
    assert mapping == {"mode": "Mode", "from_hub": "From_Hub", "mode_2": "mode"}


def test_map_columns_uses_synonyms():
    cols = dl.map_columns(["origin", "destination", "lat", "lng", "oneway"])
    assert cols == {"from_hub": "origin", "to_hub": "destination", "latitude": "lat",
                    "longitude": "lng", "one_way": "oneway"}


def test_to_float_lenient():
    assert dl.to_float("1,200") == 1200
    assert dl.to_float("x0.85") == 0.85
    assert dl.to_float("Tk 50") == 50
    assert dl.to_float("abc") is None
    assert dl.strict_float("x0.85") is None


def test_tsv_delimiter_and_kind(tmp_path):
    p = tmp_path / "metro.tsv"
    p.write_text("From/To\tUttara North\tMirpur 10\tFarmgate\nUttara North\t0\t1\t2\n"
                 "Mirpur 10\t1\t0\t3\nFarmgate\t2\t3\t0\n", encoding="utf-8")
    [sheet] = dl.read_grid(p)
    assert sheet["delimiter"] == "\t"
    assert dl.detect_kind(p, sheet["rows"])[0] == dm.METRO_FARE_MATRIX


def test_xlsx_sheets_are_inspected_separately(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fares"
    ws.append(dm.EXPECTED_FARE_COLUMNS)
    ws.append(["Rickshaw", "Non-app", "x", "A", "B"] + [1] * (len(dm.EXPECTED_FARE_COLUMNS) - 5))
    ws2 = wb.create_sheet("Metro")
    ws2.append(["From/To", "Uttara North", "Mirpur 10", "Farmgate"])
    for row in (["Uttara North", 0, 1, 2], ["Mirpur 10", 1, 0, 3], ["Farmgate", 2, 3, 0]):
        ws2.append(row)
    path = tmp_path / "book.xlsx"
    wb.save(path)
    entries = dl.inspect_file(path, "root", "fixtures")
    assert [(e["sheet"], e["detected_kind"]) for e in entries] == [
        ("Fares", dm.TRANSPORT_FARES), ("Metro", dm.METRO_FARE_MATRIX)]
    assert entries[0]["missing_required_columns"] == []
    assert entries[0]["row_count"] == 1


def test_headerless_polyline_trailing_numeric_inference():
    row = ["DhakaStreet", "90.404772", "23.855136", "90.404618", "23.855031", "0", "0.04683"]
    p = dl.split_polyline_row(row)
    assert p["coords"] == [(90.404772, 23.855136), (90.404618, 23.855031)]
    assert p["attrs"] == [0.0, 0.04683]
    named = dl.split_polyline_row(["Bus", "90.1", "23.1", "90.2", "23.2", "Rajuk", " Azampur"])
    assert named["texts"] == ["Rajuk", "Azampur"] and named["attrs"] == []


def test_polyline_layout_analysis_infers_lon_lat():
    rows = [["L", "90.40", "23.85", "90.41", "23.86", "0", "1.2"],
            ["L", "90.41", "23.86", "90.42", "23.87", "0", "1.1"]]
    assert dl.is_polyline_grid(rows)
    layout = dl.analyze_polyline(rows)
    assert layout["coordinate_order_inferred"] == "lon_lat"
    assert layout["trailing_numeric_field_counts"] == {2: 2}


def test_table_with_header_is_not_polyline():
    rows = [["name", "a", "b", "c", "d"], ["x", "1", "2", "3", "4"]]
    assert not dl.is_polyline_grid(rows)


def test_rule_sections_both_layouts():
    column_layout = [["Section", "Item", "Value", "Meaning"],
                     ["Hub wealth index", "Motijheel", "1.05", "x"],
                     ["Mode rates", "Rickshaw", "base 15 + 25/km, min 30, 9 km/h", "BDT; low/high band x0.85/1.25"],
                     ["Rule", "Student", "50% off bus only, min Tk 10", ""]]
    kinds = [s["kind"] for s in dl.rule_sections(column_layout)]
    assert kinds == ["wealth", "mode_rates", "rules"]
    title_layout = [["Mode rates"], ["Rickshaw: base 15 + 25/km, min 30, 9 km/h, low/high band x0.85/1.25"],
                    ["Multipliers"], ["Rickshaw: rain x1.6, peak x1.25"]]
    assert [s["kind"] for s in dl.rule_sections(title_layout)] == ["mode_rates", "multipliers"]


def test_sequential_id_column_detected():
    table = {"normalized_headers": ["n", "mode"],
             "rows": [{"n": "1", "mode": "a"}, {"n": "2", "mode": "b"}, {"n": "3", "mode": "c"}]}
    assert dl.sequential_id_columns(table) == ["n"]


def test_fixture_manifest():
    manifest = dl.build_manifest("fixtures")
    kinds = {Path(d["file_path"]).name: d["detected_kind"] for d in manifest["datasets"]}
    assert kinds == {
        "Dhaka_Transport_Fares_TEST_FIXTURE.csv": dm.TRANSPORT_FARES,
        "Dhaka_Metro_Fare_Matrix_TEST_FIXTURE.csv": dm.METRO_FARE_MATRIX,
        "Section_Rules_TEST_FIXTURE.csv": dm.SECTION_RULES,
        "Routemap-TestFixtureLine_TEST_FIXTURE.csv": dm.COORD_POLYLINE,
        "TEST_FIXTURE_edges.csv": dm.COORD_EDGES,
    }
    assert all(d["is_test_fixture"] and not d["production_data"] for d in manifest["datasets"])
    assert manifest["missing_dataset_groups"] == []
    for d in manifest["datasets"]:
        for key in ("file_path", "file_type", "detected_kind", "headers", "row_count",
                    "missing_required_columns", "warnings", "is_test_fixture"):
            assert key in d


def test_missing_roots_do_not_crash(tmp_path):
    roots = {"root": tmp_path / "nope", "datasets": tmp_path / "nope2", "coordination": tmp_path / "nope3"}
    manifest = dl.build_manifest("production", roots)
    assert manifest["datasets"] == []
    assert sorted(manifest["missing_dataset_groups"]) == sorted(dm.DATASET_GROUPS)
    assert any("co-ordination folder not found" in w for w in manifest["warnings"])


def test_xls_and_unreadable_files_are_reported_not_raised(tmp_path):
    (tmp_path / "old.xls").write_bytes(b"not really excel")
    [entry] = dl.inspect_file(tmp_path / "old.xls", "root", "production")
    assert entry["detected_kind"] == dm.UNKNOWN
    assert "xls" in entry["warnings"][0]


def test_production_manifest_never_includes_fixtures():
    manifest = dl.build_manifest("production")
    assert not any("tests/fixtures" in d["file_path"] for d in manifest["datasets"])


def test_legacy_csv_folder_is_flagged(tmp_path):
    (tmp_path / "csv").mkdir()
    manifest = dl.build_manifest("production", {"root": tmp_path, "coordination": tmp_path / "co-ordination"})
    assert any("backend/csv exists" in w for w in manifest["warnings"])
