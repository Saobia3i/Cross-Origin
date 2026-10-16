"""Dataset discovery, format detection, header inspection and raw reading.

Read-only: this module never writes to raw dataset files.
"""
from __future__ import annotations

import csv
import io
import math
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path

from app.services import dataset_manifest as dm

csv.field_size_limit(10_000_000)

FILE_TYPES = {".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls"}
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

# Canonical field -> accepted (already snake_cased) header names, in priority order.
# dataset_config/coordination_mapping.json may override via "column_synonyms".
COLUMN_SYNONYMS: dict[str, list[str]] = {
    "route_id": ["route_id", "edge_id", "route_code", "route"],
    "mode": ["mode", "transport_mode", "vehicle", "vehicle_type"],
    "provider": ["provider", "operator", "company"],
    "category": ["category"],
    "line_name": ["line_name", "line", "route_name"],
    "from_hub": ["from_hub", "from", "origin", "source", "from_stop", "from_station", "start", "start_hub"],
    "to_hub": ["to_hub", "to", "destination", "target", "to_stop", "to_station", "end", "end_hub"],
    "hub_id": ["hub_id", "stop_id", "station_id"],
    "name": ["name", "hub_name", "hub", "stop_name", "stop", "station_name", "station"],
    "name_bn": ["name_bn", "bangla_name", "name_bangla", "bn_name"],
    "latitude": ["latitude", "lat", "y"],
    "longitude": ["longitude", "lng", "lon", "long", "x"],
    "geometry": ["geometry", "wkt", "geojson", "geom", "the_geom", "shape"],
    "stops": ["stops", "via", "stop_list", "via_stops"],
    "distance_km": ["distance_km", "road_distance_km", "length_km", "distance"],
    "time_min": ["time_min", "travel_time_min", "duration_min", "time", "duration"],
    "direction": ["direction", "dir"],
    "one_way": ["one_way", "oneway", "is_one_way"],
    "status": ["status", "state"],
}

_FARE_HINT = re.compile(r"transport[_\s-]*fares?|fare[_\s-]*matrix|fares?", re.I)
_METRO_HINT = re.compile(r"metro", re.I)
_RULES_HINT = re.compile(r"section|rules?|mode[_\s-]*rates?", re.I)

SECTION_TITLE_PATTERNS = [
    ("wealth", re.compile(r"wealth", re.I)),
    ("multipliers", re.compile(r"multiplier", re.I)),
    ("mode_rates", re.compile(r"mode\s*rates?|\brates?\b", re.I)),
    ("rules", re.compile(r"\brules?\b", re.I)),
]


class UnsupportedFormat(Exception):
    pass


# --- small value helpers -------------------------------------------------------
def strict_float(value) -> float | None:
    """Plain numeric literal only (used for geometry): '23.8' ok, 'x1.6' not."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def to_float(value) -> float | None:
    """Lenient numeric parse for table cells: '1,200', 'Tk 50', 'x0.85', '9 km/h'."""
    v = strict_float(value)
    if v is not None or value is None:
        return v
    s = str(value).strip().lower().replace(",", "").replace("৳", "")
    s = re.sub(r"^(tk\.?|bdt|x)\s*", "", s)
    s = re.sub(r"\s*(tk|bdt|km/h|kmh|km|min|%)$", "", s)
    return strict_float(s)


def cell_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def non_empty(row) -> list[str]:
    return [str(c).strip() for c in row if str(c).strip()]


# --- headers -----------------------------------------------------------------
def normalize_header(header, index: int = 0) -> str:
    """'Road_Distance_km' -> road_distance_km, 'Peak Hour (min)' -> peak_hour_min, 'fromHub' -> from_hub."""
    text = unicodedata.normalize("NFKD", cell_str(header)).encode("ascii", "ignore").decode().strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])|(?<=[a-z])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or f"column_{index + 1}"


def normalize_headers(headers) -> tuple[list[str], dict[str, str]]:
    """Returns (normalized names, {normalized: original}). Duplicates get _2, _3 ..."""
    out, mapping, seen = [], {}, Counter()
    for i, h in enumerate(headers):
        name = normalize_header(h, i)
        seen[name] += 1
        if seen[name] > 1:
            name = f"{name}_{seen[name]}"
        out.append(name)
        mapping[name] = cell_str(h)
    return out, mapping


def map_columns(normalized_headers, synonyms: dict[str, list[str]] | None = None) -> dict[str, str]:
    """{canonical_field: actual_normalized_header} for headers matching COLUMN_SYNONYMS."""
    synonyms = synonyms or COLUMN_SYNONYMS
    present, out, used = set(normalized_headers), {}, set()
    for field, names in synonyms.items():
        for n in names:
            if n in present and n not in used:
                out[field] = n
                used.add(n)
                break
    return out


def find_header_row(rows, max_scan: int = 30) -> int | None:
    """First row with >=2 non-empty cells, mostly non-numeric."""
    for i, row in enumerate(rows[:max_scan]):
        cells = non_empty(row)
        if len(cells) >= 2 and sum(strict_float(c) is None for c in cells) * 2 >= len(cells):
            return i
    return None


# --- file reading ------------------------------------------------------------
def detect_file_type(path: Path) -> str | None:
    return FILE_TYPES.get(Path(path).suffix.lower())


def read_text(path: Path) -> tuple[str, str]:
    data = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def sniff_delimiter(text: str, file_type: str) -> str:
    if file_type == "tsv":
        return "\t"
    sample = text[:20000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        counts = {d: first.count(d) for d in ",\t;|"}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_grid(path: Path) -> list[dict]:
    """[{sheet, rows, encoding, delimiter}] - one item for CSV/TSV, one per sheet for Excel."""
    path = Path(path)
    file_type = detect_file_type(path)
    if file_type in ("csv", "tsv"):
        text, enc = read_text(path)
        delim = sniff_delimiter(text, file_type)
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        return [{"sheet": None, "rows": rows, "encoding": enc, "delimiter": delim}]
    if file_type == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = []
            for ws in wb.worksheets:
                rows = []
                for r in ws.iter_rows(values_only=True):
                    row = [cell_str(v) for v in r]
                    while row and row[-1] == "":
                        row.pop()
                    rows.append(row)
                while rows and not rows[-1]:
                    rows.pop()
                sheets.append({"sheet": ws.title, "rows": rows, "encoding": None, "delimiter": None})
            return sheets
        finally:
            wb.close()
    if file_type == "xls":
        raise UnsupportedFormat("legacy .xls is not supported; re-save as .xlsx or .csv")
    raise UnsupportedFormat(f"unsupported file type: {path.suffix}")


# --- headerless polyline files (official co-ordination dataset) ---------------
def infer_trailing_numeric(nums: list[float]) -> int:
    """Count trailing numeric fields that cannot be part of the coordinate list.

    A trailing pair is treated as non-coordinate when it lies > 1 degree from the
    first point (e.g. '..., 90.40, 23.85, 0, 0.0468'); an odd count peels one value.
    """
    n = 0
    while len(nums) - n >= 4:
        k = len(nums) - n
        if k % 2:
            n += 1
            continue
        if abs(nums[k - 2] - nums[0]) > 1 or abs(nums[k - 1] - nums[1]) > 1:
            n += 2
            continue
        break
    return n


def split_polyline_row(row, trailing_numeric: int | None = None) -> dict | None:
    """'layer, x, y, x, y, ..., [numeric attrs], [text fields]' -> parts, or None."""
    cells = [str(c).strip() for c in row]
    while cells and cells[-1] == "":
        cells.pop()
    if len(cells) < 5 or strict_float(cells[0]) is not None:
        return None
    rest = cells[1:]
    texts: list[str] = []
    while rest and strict_float(rest[-1]) is None:
        texts.insert(0, rest.pop())
    nums = [strict_float(c) for c in rest]
    if any(v is None for v in nums):
        return None
    if trailing_numeric is None:
        trailing_numeric = infer_trailing_numeric(nums)
    cut = len(nums) - trailing_numeric
    coord_vals, attrs = nums[:cut], nums[cut:]
    if len(coord_vals) < 4 or len(coord_vals) % 2:
        return None
    coords = [(coord_vals[i], coord_vals[i + 1]) for i in range(0, len(coord_vals), 2)]
    return {"layer": cells[0], "coords": coords, "texts": texts, "attrs": attrs}


def is_polyline_grid(rows) -> bool:
    sample = [r for r in rows if non_empty(r)][:200]
    if not sample or split_polyline_row(sample[0]) is None:  # a header row means it is a table
        return False
    ok = sum(split_polyline_row(r) is not None for r in sample)
    return ok / len(sample) >= 0.9


def haversine_m(a, b) -> float:
    """Distance in metres between (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def line_length_km(coords) -> float:
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1)) / 1000


def _pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def analyze_polyline(rows) -> dict:
    """Layout statistics used to build/validate dataset_config/coordination_mapping.json."""
    parsed = [p for p in (split_polyline_row(r) for r in rows if non_empty(r)) if p]
    if not parsed:
        return {}
    firsts = [c[0] for p in parsed for c in p["coords"]]
    seconds = [c[1] for p in parsed for c in p["coords"]]
    if any(abs(v) > 90 for v in firsts) and all(abs(v) <= 90 for v in seconds):
        order = "lon_lat"
    elif any(abs(v) > 90 for v in seconds) and all(abs(v) <= 90 for v in firsts):
        order = "lat_lon"
    else:
        order = "undetermined"
    attr_counts = Counter(len(p["attrs"]) for p in parsed)
    text_counts = Counter(len(p["texts"]) for p in parsed)
    n_attrs = attr_counts.most_common(1)[0][0]
    attr_stats = []
    for i in range(n_attrs):
        vals = [p["attrs"][i] for p in parsed if len(p["attrs"]) == n_attrs]
        stat = {"index_from_end": n_attrs - i, "min": min(vals), "max": max(vals),
                "distinct_values": len(set(vals))}
        if len(set(vals)) <= 5:
            stat["values"] = sorted(set(vals))
        if order != "undetermined":
            ratios = []
            for p in parsed:
                if len(p["attrs"]) != n_attrs:
                    continue
                coords = p["coords"] if order == "lon_lat" else [(y, x) for x, y in p["coords"]]
                length = line_length_km(coords)
                if length > 0.01:
                    ratios.append(p["attrs"][i] / length)
            if ratios and len(set(vals)) > 5:
                stat["ratio_to_geometry_length_km"] = {
                    "median": round(statistics.median(ratios), 4),
                    "p05": round(_pct(ratios, 0.05), 4), "p95": round(_pct(ratios, 0.95), 4)}
        attr_stats.append(stat)
    return {
        "layout": "headerless_polyline",
        "parsed_rows": len(parsed),
        "layers": dict(Counter(p["layer"] for p in parsed)),
        "coordinate_order_inferred": order,
        "coordinate_bbox": [min(firsts), min(seconds), max(firsts), max(seconds)],
        "points_per_row": {"min": min(len(p["coords"]) for p in parsed),
                           "max": max(len(p["coords"]) for p in parsed)},
        "trailing_numeric_field_counts": dict(attr_counts),
        "trailing_numeric_field_stats": attr_stats,
        "trailing_text_field_counts": dict(text_counts),
        "rows_with_single_text_field": [i for i, p in enumerate(parsed) if len(p["texts"]) == 1],
        "sample_trailing_text": [p["texts"] for p in parsed[:3]],
    }


# --- section-style rules files -------------------------------------------------
def section_kind_of_title(text: str) -> str | None:
    for kind, pattern in SECTION_TITLE_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def _is_title_row(cells: list[str]) -> str | None:
    if not cells or len(cells) > 2:
        return None
    text = " ".join(cells)
    if len(text) > 60 or "=" in text or re.search(r"x\s*\d", text) or any(strict_float(c) is not None for c in cells):
        return None
    return section_kind_of_title(text)


def split_sections(rows, sheet_name: str | None = None) -> list[dict]:
    """Split a grid into [{kind, title, rows}] using short title rows ('Mode rates', ...).

    Rows before any title land in a section with kind None. A sheet whose name is a
    section title ('Multipliers') starts in that section.
    """
    first_kind = section_kind_of_title(sheet_name) if sheet_name else None
    current = {"kind": first_kind, "title": sheet_name if first_kind else None, "rows": []}
    sections = [current]
    for row in rows:
        cells = non_empty(row)
        if not cells:
            continue
        kind = _is_title_row(cells)
        if kind and kind == current["kind"] and not current["rows"]:
            continue  # e.g. the 'Rule,Value' header right after a 'Rules' title
        if kind:
            current = {"kind": kind, "title": " ".join(cells), "rows": []}
            sections.append(current)
            continue
        current["rows"].append([str(c).strip() for c in row])
    return [s for s in sections if s["rows"] or s["kind"]]


def rule_sections(rows, sheet_name: str | None = None) -> list[dict]:
    """Sections of a rules grid, in either layout:

    * section column: header like 'Section,Item,Value,Meaning'; each row names its section
      (the layout of Dhaka_Fare_Assumptions.csv). Rows are returned without that column.
    * title rows: a short row 'Mode rates' starts a section (see split_sections).
    """
    hi = find_header_row(rows)
    if hi is not None:
        norm, _ = normalize_headers(rows[hi])
        if "section" in norm and len(norm) >= 3:
            idx = norm.index("section")
            sections: list[dict] = []
            by_title: dict[str, dict] = {}
            for row in rows[hi + 1:]:
                cells = [str(c).strip() for c in row]
                if not any(cells):
                    continue
                title = cells[idx] if idx < len(cells) else ""
                if title not in by_title:
                    by_title[title] = {"kind": section_kind_of_title(title) if title else None,
                                       "title": title or None, "rows": []}
                    sections.append(by_title[title])
                by_title[title]["rows"].append([c for j, c in enumerate(cells) if j != idx])
            return sections
    return split_sections(rows, sheet_name)


def sequential_id_columns(table: dict) -> list[str]:
    """Columns whose values are exactly 1..N (or k..k+N-1): row numbers, not data."""
    out = []
    for h in table["normalized_headers"]:
        vals = [r.get(h, "") for r in table["rows"]]
        if len(vals) >= 3 and all(re.fullmatch(r"\d+", v or "") for v in vals):
            start = int(vals[0])
            if [int(v) for v in vals] == list(range(start, start + len(vals))):
                out.append(h)
    return out


# --- kind detection ------------------------------------------------------------
def _metro_station_matcher():
    """Callable: is this cell a known metro station (after required/approved aliases)?"""
    from app.services.hub_alias import HubRegistry

    registry = HubRegistry()
    stations = {registry.group_key(s) for s in dm.load_config("problem_owner_rules.json").get("metro_stations", [])}
    return lambda cell: bool(str(cell).strip()) and registry.group_key(cell) in stations


def detect_kind(path: Path, rows, sheet_name: str | None = None) -> tuple[str, str]:
    """(kind, human-readable reason). Headers win over filename hints."""
    hint_text = f"{Path(path).stem} {sheet_name or ''}"
    if is_polyline_grid(rows):
        return dm.COORD_POLYLINE, "headerless rows of: text layer, numeric coordinate pairs, trailing fields"
    hi = find_header_row(rows)
    if hi is None:
        return dm.UNKNOWN, "no header row found"
    header = rows[hi]
    norm, _ = normalize_headers(header)
    norm_set = set(norm)

    is_station = _metro_station_matcher()
    header_hits = sum(is_station(c) for c in header[1:])
    col_hits = sum(is_station(r[0]) for r in rows[hi + 1:] if r)
    cols = map_columns(norm)
    long_metro = {"from_hub", "to_hub"} <= set(cols) and any("fare" in h or "bdt" in h for h in norm)
    if "from_hub" not in norm_set and (header_hits >= 3 or col_hits >= 3):
        return dm.METRO_FARE_MATRIX, f"matrix with {header_hits} known metro stations in header, {col_hits} in first column"
    if long_metro and _METRO_HINT.search(hint_text):
        return dm.METRO_FARE_MATRIX, "long-form from/to/fare table with 'metro' in name"

    expected = {normalize_header(c) for c in dm.EXPECTED_FARE_COLUMNS}
    bdt_cols = [h for h in norm if h.endswith("_bdt")]
    if {"mode", "from_hub", "to_hub"} <= norm_set and len(bdt_cols) >= 2 or len(norm_set & expected) >= 8:
        return dm.TRANSPORT_FARES, f"{len(norm_set & expected)}/{len(expected)} expected fare columns present"

    text = " ".join(" ".join(non_empty(r)) for r in rows[:300]).lower()
    score = sum(k in text for k in ("wealth", "multiplier", "/km", "per km", "per_km", "base", "festive", "detour"))
    if score >= 3 or (score >= 1 and _RULES_HINT.search(hint_text)):
        return dm.SECTION_RULES, f"rules keywords score {score}"

    if {"from_hub", "to_hub"} <= set(cols):
        return dm.COORD_EDGES, "headered table with from/to columns"
    if {"latitude", "longitude"} <= set(cols):
        return dm.COORD_HUBS, "headered table with latitude/longitude columns"
    hints = [n for n, p in (("fares", _FARE_HINT), ("metro", _METRO_HINT), ("rules", _RULES_HINT)) if p.search(hint_text)]
    return dm.UNKNOWN, f"headers not recognised (filename hints: {hints or 'none'})"


# --- inspection ---------------------------------------------------------------
def discover_files(roots: dict[str, Path]) -> list[tuple[Path, str]]:
    found: dict[Path, str] = {}

    def wanted(p: Path) -> bool:
        return (p.is_file() and detect_file_type(p) is not None
                and not p.name.startswith((".", "~$")))

    root = roots.get("root")
    if root and Path(root).is_dir():
        for p in sorted(Path(root).iterdir()):
            if wanted(p):
                found.setdefault(p.resolve(), "root")
    for location in ("datasets", "coordination"):
        d = roots.get(location)
        if d and Path(d).is_dir():
            for p in sorted(Path(d).rglob("*")):
                if wanted(p) and ".venv" not in p.parts:
                    found.setdefault(p.resolve(), location)
    return sorted(found.items(), key=lambda kv: (kv[1], kv[0].as_posix()))


def _required_missing(kind: str, norm: list[str], rows, sheet) -> list[str]:
    if kind == dm.TRANSPORT_FARES:
        present = set(norm)
        return [c for c in dm.EXPECTED_FARE_COLUMNS if normalize_header(c) not in present]
    if kind == dm.COORD_EDGES:
        cols = map_columns(norm)
        return [f for f in ("from_hub", "to_hub") if f not in cols]
    if kind == dm.COORD_HUBS:
        cols = map_columns(norm)
        return [f for f in ("name", "latitude", "longitude") if f not in cols]
    if kind == dm.SECTION_RULES:
        kinds = {s["kind"] for s in rule_sections(rows, sheet)}
        return [f"section:{k}" for k in dm.REQUIRED_RULE_SECTIONS if k not in kinds]
    return []


def inspect_file(path: Path, location: str, source: str) -> list[dict]:
    path = Path(path)
    file_type = detect_file_type(path)
    is_fixture = source == "fixtures" or dm.is_fixture_path(path)
    base = {
        "file_path": dm.display_path(path), "file_name": path.name, "location": location,
        "file_type": file_type, "is_test_fixture": is_fixture, "production_data": not is_fixture,
    }
    try:
        sheets = read_grid(path)
    except Exception as exc:  # unreadable file must not stop inspection
        return [{**base, "id": base["file_path"], "sheet": None, "detected_kind": dm.UNKNOWN,
                 "detection_reason": "unreadable", "headers": [], "row_count": None,
                 "missing_required_columns": [], "warnings": [f"could not read file: {exc}"]}]
    entries = []
    for sh in sheets:
        rows, sheet = sh["rows"], sh["sheet"]
        kind, reason = detect_kind(path, rows, sheet)
        entry = {**base, "id": base["file_path"] + (f"#{sheet}" if sheet else ""), "sheet": sheet,
                 "encoding": sh["encoding"], "delimiter": sh["delimiter"],
                 "detected_kind": kind, "detection_reason": reason, "warnings": []}
        if is_fixture and source == "production":
            entry["warnings"].append("test fixture found among production files; it will be skipped")
        if kind == dm.COORD_POLYLINE:
            layout = analyze_polyline(rows)
            entry.update(header_mode="headerless", header_row_index=None, headers=None,
                         normalized_headers=None, row_count=sum(1 for r in rows if non_empty(r)),
                         layout=layout, missing_required_columns=[])
            if layout.get("coordinate_order_inferred") == "undetermined":
                entry["warnings"].append("coordinate order (lon,lat vs lat,lon) could not be inferred")
            if layout.get("rows_with_single_text_field"):
                entry["warnings"].append(
                    f"{len(layout['rows_with_single_text_field'])} row(s) have one trailing text field "
                    "instead of from/to names")
        else:
            hi = find_header_row(rows)
            headers = [cell_str(c) for c in rows[hi]] if hi is not None else []
            norm, _ = normalize_headers(headers)
            data_rows = [r for r in rows[(hi + 1 if hi is not None else 0):] if non_empty(r)]
            entry.update(header_mode="header_row" if hi is not None else "none", header_row_index=hi,
                         headers=headers, normalized_headers=norm, row_count=len(data_rows),
                         missing_required_columns=_required_missing(kind, norm, rows, sheet))
            if kind in (dm.COORD_EDGES, dm.COORD_HUBS, dm.TRANSPORT_FARES):
                entry["inferred_mapping"] = map_columns(norm)
            if kind == dm.TRANSPORT_FARES:
                expected = {normalize_header(c) for c in dm.EXPECTED_FARE_COLUMNS}
                extra = [h for h in norm if h not in expected]
                entry["unexpected_columns"] = extra
                table = {"normalized_headers": extra,
                         "rows": [{h: (str(r[norm.index(h)]).strip() if norm.index(h) < len(r) else "")
                                   for h in extra} for r in data_rows]}
                for h in sequential_id_columns(table):
                    entry["warnings"].append(
                        f"column {headers[norm.index(h)]!r} holds sequential row numbers 1..{len(data_rows)}; "
                        "treated as a row id, not as data")
            if kind == dm.SECTION_RULES:
                entry["sections"] = [{"kind": s["kind"], "title": s["title"], "rows": len(s["rows"])}
                                     for s in rule_sections(rows, sheet)]
                for s in entry["sections"]:
                    if s["kind"] is None and s["title"]:
                        entry["warnings"].append(f"unrecognised rules section {s['title']!r}")
            if kind == dm.METRO_FARE_MATRIX:
                from app.services.hub_alias import HubRegistry

                reg = HubRegistry()
                stations = {reg.group_key(c) for c in headers[1:]} | {reg.group_key(r[0]) for r in data_rows if r}
                known = dm.load_config("problem_owner_rules.json").get("metro_stations", [])
                missing = [s for s in known if reg.group_key(s) not in stations]
                if missing:
                    entry["warnings"].append(f"known metro stations not found in matrix: {missing}")
            if kind == dm.UNKNOWN:
                entry["warnings"].append("dataset kind not recognised; file will be ignored by normalization")
        if entry.get("missing_required_columns"):
            entry["warnings"].append(f"missing required columns: {entry['missing_required_columns']}")
        entries.append(entry)
    return entries


def build_manifest(source: str = "production", roots: dict[str, Path] | None = None) -> dict:
    roots = {k: Path(v) for k, v in (roots or dm.default_roots(source)).items()}
    datasets: list[dict] = []
    for path, location in discover_files(roots):
        datasets.extend(inspect_file(path, location, source))
    groups = {}
    for group, kinds in dm.DATASET_GROUPS.items():
        ids = [d["id"] for d in datasets
               if d["detected_kind"] in kinds and (source == "fixtures" or not d["is_test_fixture"])]
        groups[group] = {"present": bool(ids), "entries": ids}
    warnings = []
    if roots.get("coordination") and not Path(roots["coordination"]).is_dir():
        warnings.append(f"co-ordination folder not found: {dm.display_path(roots['coordination'])}")
    legacy_csv = Path(roots["root"]) / "csv" if roots.get("root") else None
    if legacy_csv and legacy_csv.is_dir():
        warnings.append("backend/csv exists; rename it to backend/co-ordination (see docs/MERGE_NOTES.md)")
    missing = [g for g, info in groups.items() if not info["present"]]
    for g in missing:
        warnings.append(f"dataset not found: {g}")
    return {
        "schema_version": 1,
        "generated_at": dm.utc_now(),
        "source": source,
        "roots": {k: dm.display_path(v) for k, v in roots.items()},
        "datasets": datasets,
        "dataset_groups": groups,
        "missing_dataset_groups": missing,
        "warnings": warnings,
    }


def load_table(entry: dict) -> dict:
    """Re-read a headered dataset entry -> {headers, normalized_headers, raw_mapping, rows}."""
    sheets = read_grid(dm.resolve_path(entry["file_path"]))
    sheet = next(s for s in sheets if s["sheet"] == entry.get("sheet"))
    rows = sheet["rows"]
    hi = entry.get("header_row_index")
    hi = find_header_row(rows) if hi is None else hi
    if hi is None:
        return {"headers": [], "normalized_headers": [], "raw_mapping": {}, "rows": [], "grid": rows}
    headers = [cell_str(c) for c in rows[hi]]
    norm, mapping = normalize_headers(headers)
    out = []
    for offset, r in enumerate(rows[hi + 1:]):
        if not non_empty(r):
            continue
        rec = {norm[i]: (str(r[i]).strip() if i < len(r) else "") for i in range(len(norm))}
        rec["_row_number"] = hi + offset + 2  # 1-based line/row number in the file
        out.append(rec)
    return {"headers": headers, "normalized_headers": norm, "raw_mapping": mapping, "rows": out, "grid": rows}


def load_grid(entry: dict) -> list[list[str]]:
    sheets = read_grid(dm.resolve_path(entry["file_path"]))
    return next(s for s in sheets if s["sheet"] == entry.get("sheet"))["rows"]
