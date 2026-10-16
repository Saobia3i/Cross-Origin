"""Normalize inspected datasets into JSON artifacts under data/generated/.

Pipeline: manifest -> parse every dataset (raw names kept) -> register every hub name ->
build hubs, route edges, fare rows, metro fares, rules -> write JSON + normalization_report.json.

Nothing here invents data: values missing from the sources stay null. Coordinates come
only from the official co-ordination dataset (explicit lat/lng columns, or endpoints of
official route geometry), never from guesses.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from app.services import dataset_loader as dl
from app.services import dataset_manifest as dm
from app.services.hub_alias import HubRegistry, clean_name, compact_key, load_alias_config, make_hub_id

SCENARIOS = ["normal", "rain", "peak", "peak_plus_rain", "detour", "regular_holiday", "festive_holiday"]

# Dhaka_Transport_Fares column prefix (snake_case) for each fare scenario.
FARE_SCENARIO_PREFIX = {
    "normal": "normal",
    "rain": "rain",
    "peak": "peak_office_hour",
    "peak_plus_rain": "peak_plus_rain",
    "detour": "blockage_construction_waterlog_detour",
    "regular_holiday": "regular_holiday",
    "festive_holiday": "festive_holiday",
    "student": "student_fare",
}

OUTPUT_FILES = {
    "hubs": "normalized_hubs.json",
    "route_edges": "normalized_route_edges.json",
    "fare_rows": "normalized_fare_rows.json",
    "metro_fares": "normalized_metro_fare_matrix.json",
    "mode_rates": "normalized_mode_rates.json",
    "multipliers": "normalized_multipliers.json",
    "wealth_indices": "normalized_wealth_indices.json",
    "rules": "normalized_rules.json",
    "road_segments": "normalized_road_segments.json",
}
REPORT_FILE = "normalization_report.json"

ASSUMPTIONS = [
    "Headerless co-ordination rows are read as: layer, x1, y1, x2, y2, ..., trailing fields "
    "(dataset_config/coordination_mapping.json). Coordinate order is checked against the data.",
    "Route-file trailing text fields are (from_stop, to_stop) in listed order; edge topology follows the listing.",
    "Geometry vertex order is often opposite to the listed from->to. Orientation is inferred only from "
    "shared endpoints with neighbouring rows of the same layer; the geometry itself is stored as provided.",
    "Hub coordinates from route geometry use only endpoints whose orientation was inferred; endpoints at a "
    "junction where neighbouring rows disagree on the stop name are excluded.",
    "Direction (one-way vs bidirectional) is not stated by the official route files, so those edges are "
    "direction_unknown=true.",
    "A single trailing stop field like 'Farm gate. Karwan Bazar' is split on '.' into from/to (warned).",
    "Road-network trailing values are kept raw (attr_flag_raw, attr_cost_raw); their meaning is unconfirmed.",
    "Multiplier 'normal' = 1.0 is the implicit baseline. 'peak_plus_rain' is null unless a source states it.",
]


class Context:
    def __init__(self, source: str):
        self.source = source
        self.warnings: list[str] = []
        self.column_mappings: dict[str, dict[str, str]] = {}
        self.label_conflicts: list[dict] = []
        self.coordinate_conflicts: list[dict] = []
        self.datasets: dict[str, dict] = {}

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# =============================================================================
# Modes
# =============================================================================
def _mode_key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


class ModeResolver:
    def __init__(self, config: dict):
        self.info: dict[str, dict] = {}
        self._map: dict[str, str] = {}
        for code, info in config.items():
            if code.startswith("_"):
                continue
            self.info[code] = info
            for name in [code, *info.get("names", [])]:
                self._map[_mode_key(name)] = code

    def resolve(self, *candidates) -> str | None:
        for c in candidates:
            if not clean_name(c):
                continue
            for variant in (c, re.sub(r"\(.*?\)", "", str(c))):
                code = self._map.get(_mode_key(variant))
                if code:
                    return code
        return None

    def mode_of(self, code: str | None) -> str | None:
        return self.info.get(code, {}).get("mode") if code else None


def _fallback_mode_code(name) -> str:
    return make_hub_id(re.sub(r"\(.*?\)", "", str(name))) if clean_name(name) else "UNKNOWN"


# =============================================================================
# Section rules (wealth index, mode rates, multipliers, rules)
# =============================================================================
_NUM = r"(\d+(?:\.\d+)?)"
_N = _NUM[1:-1]
# Body of a mode-rate line, e.g. "base 15 + 25/km, min 30, 9 km/h, BDT; low/high band x0.85/1.25"
MODE_RATE_BODY_RE = re.compile(
    rf"base\s*(?P<base>{_N})\s*\+\s*(?P<per_km>{_N})\s*/\s*km\s*,?\s*min(?:imum)?\s*(?P<min>{_N})\s*,?\s*"
    rf"(?P<speed>{_N})\s*km\s*/?\s*h\b.*?band\s*x?\s*(?P<low>{_N})\s*/\s*x?\s*(?P<high>{_N})",
    re.I,
)
MODE_RATE_RE = re.compile(rf"^(?P<mode>[^:]+?)\s*[:,]\s*{MODE_RATE_BODY_RE.pattern}", re.I)
DECLARED_ALIAS_RE = re.compile(r"mapping\s*:\s*(.+)$", re.I)
MULT_TERM_RE = re.compile(
    rf"(peak\s*(?:\+|plus|and)\s*rain|regular\s*holiday|festive(?:\s*holiday)?|holiday|rain|peak|detour|"
    rf"blockage|normal)\s*[:=]?\s*x\s*{_NUM}",
    re.I,
)
WEALTH_RE = re.compile(rf"^(?P<name>[^:=]+?)\s*[:=]\s*(?P<value>{_NUM[1:-1]})\s*$")
RULE_RE = re.compile(r"^(?P<key>[^=]{1,60}?)\s*=\s*(?P<value>.+)$")


def scenario_key(text: str) -> str | None:
    t = str(text).lower()
    if "peak" in t and "rain" in t:
        return "peak_plus_rain"
    if "festive" in t:
        return "festive_holiday"
    if "holiday" in t:
        return "regular_holiday"
    if "detour" in t or "blockage" in t:
        return "detour"
    if "rain" in t:
        return "rain"
    if "peak" in t:
        return "peak"
    if "normal" in t:
        return "normal"
    return None


def _band(value) -> tuple[float | None, float | None]:
    text = str(value or "")
    if "/" in text:
        parts = [dl.to_float(p) for p in text.split("/", 1)]
        return parts[0], parts[1]
    return dl.to_float(text), None


MODE_RATE_COLUMNS = [
    ("mode", lambda h: h.startswith("mode") or h in ("vehicle", "transport")),
    ("base_fare_bdt", lambda h: "base" in h),
    ("per_km_bdt", lambda h: "per_km" in h or "km_rate" in h or h.endswith("_per_km") or h.startswith("rate")),
    ("min_fare_bdt", lambda h: h.startswith("min") or "minimum" in h),
    ("average_speed_kmh", lambda h: "speed" in h or "kmh" in h or "km_h" in h),
    ("low_band_multiplier", lambda h: "low" in h),
    ("high_band_multiplier", lambda h: "high" in h),
    ("band", lambda h: "band" in h),
]


def _assign_columns(norm: list[str], rules) -> dict[str, int]:
    out, used = {}, set()
    for field, pred in rules:
        for i, h in enumerate(norm):
            if i not in used and pred(h):
                out[field] = i
                used.add(i)
                break
    return out


def _header_in(rows, min_cells: int, max_scan: int = 3) -> tuple[int | None, list[str]]:
    for i, row in enumerate(rows[:max_scan]):
        cells = dl.non_empty(row)
        if len(cells) >= min_cells and all(dl.strict_float(c) is None for c in cells):
            joined = ", ".join(cells)
            if not (MODE_RATE_RE.search(joined) or MULT_TERM_RE.search(joined) or "=" in joined):
                return i, dl.normalize_headers(row)[0]
    return None, []


def _cell(row, idx):
    return row[idx].strip() if idx is not None and idx < len(row) else ""


def _parse_mode_rates(rows, src) -> list[dict]:
    hi, norm = _header_in(rows, 3)
    cols = _assign_columns(norm, MODE_RATE_COLUMNS) if hi is not None else {}
    if "mode" not in cols:
        hi = None
    out = []
    for i, row in enumerate(rows):
        if i == hi:
            continue
        rec = None
        if hi is not None and i > hi:
            mode = _cell(row, cols.get("mode"))
            if not mode:
                continue
            low, high = _band(_cell(row, cols.get("low_band_multiplier")))
            high2, _ = _band(_cell(row, cols.get("high_band_multiplier")))
            if "band" in cols and low is None:
                low, high = _band(_cell(row, cols["band"]))
            rec = {
                "mode": mode,
                "base_fare_bdt": dl.to_float(_cell(row, cols.get("base_fare_bdt"))),
                "per_km_bdt": dl.to_float(_cell(row, cols.get("per_km_bdt"))),
                "min_fare_bdt": dl.to_float(_cell(row, cols.get("min_fare_bdt"))),
                "average_speed_kmh": dl.to_float(_cell(row, cols.get("average_speed_kmh"))),
                "low_band_multiplier": low,
                "high_band_multiplier": high2 if high2 is not None else high,
                "raw_text": ", ".join(dl.non_empty(row)),
            }
        else:
            mode, body = _mode_and_body(dl.non_empty(row))
            m = MODE_RATE_BODY_RE.search(body) if mode else None
            text = ", ".join(dl.non_empty(row))
            if m:
                rec = {
                    "mode": mode,
                    "base_fare_bdt": float(m["base"]), "per_km_bdt": float(m["per_km"]),
                    "min_fare_bdt": float(m["min"]), "average_speed_kmh": float(m["speed"]),
                    "low_band_multiplier": float(m["low"]), "high_band_multiplier": float(m["high"]),
                    "raw_text": text,
                }
        if rec:
            out.append({**rec, **src, "source_row": i})
    return out


def _parse_multipliers(rows, src) -> list[dict]:
    hi, norm = _header_in(rows, 3)
    mode_col, scen_cols = None, {}
    if hi is not None:
        for i, h in enumerate(norm):
            if mode_col is None and (h.startswith("mode") or h == "vehicle"):
                mode_col = i
            elif scenario_key(h) and scenario_key(h) not in scen_cols.values():
                scen_cols[i] = scenario_key(h)
        if mode_col is None or len(scen_cols) < 2:
            hi = None
    out = []
    for i, row in enumerate(rows):
        if i == hi:
            continue
        cells = dl.non_empty(row)
        text, note = ", ".join(cells), None
        if hi is not None and i > hi:
            mode = _cell(row, mode_col)
            values = {s: dl.to_float(_cell(row, c)) for c, s in scen_cols.items() if _cell(row, c)}
        else:
            mode, body = _mode_and_body(cells)
            terms = MULT_TERM_RE.findall(body) if mode else []
            values = {scenario_key(name): float(val) for name, val in terms}
            extra = [c for c in cells[1:] if not MULT_TERM_RE.search(c)]
            note = ", ".join(extra) or None
        if mode and values:
            out.append({"mode": mode, "values": values, "note": note, "raw_text": text, **src, "source_row": i})
    return out


def _mode_and_body(cells: list[str]) -> tuple[str | None, str]:
    """'Rickshaw: rain x1.6, ...' or ['Rickshaw', 'rain x1.6, ...'] -> ('Rickshaw', 'rain x1.6, ...')."""
    if not cells:
        return None, ""
    text = ", ".join(cells)
    if ":" in cells[0]:
        mode, body = text.split(":", 1)
        return clean_name(mode), body
    if len(cells) >= 2 and not re.search(r"x\s*\d|base\s*\d", cells[0], re.I):
        return clean_name(cells[0]), ", ".join(cells[1:])
    return None, text


def _parse_wealth(rows, src) -> list[dict]:
    out = []
    for i, row in enumerate(rows):
        cells = dl.non_empty(row)
        name, value, note = None, None, None
        if len(cells) == 1:
            m = WEALTH_RE.match(cells[0])
            if m:
                name, value = m["name"], float(m["value"])
        else:
            for j, c in enumerate(cells):
                if dl.to_float(c) is None and name is None:
                    name = c
                elif name is not None and dl.to_float(c) is not None:
                    value = dl.to_float(c)
                    note = ", ".join(cells[j + 1:]) or None
                    break
        if name and value is not None:
            out.append({"hub_name": clean_name(name), "wealth_index": value, "note": note, **src, "source_row": i})
    return out


def rule_params(key: str, value: str) -> dict:
    """Machine-readable parameters extracted from a rule's text (only what the text states)."""
    v = value.lower()
    params: dict = {}
    if key == "road_distance":
        m = re.search(rf"x\s*{_NUM}", v)
        if m:
            params["straight_line_multiplier"] = float(m.group(1))
    elif key == "peak_hours":
        windows = []
        for a, b, ampm in re.findall(r"(\d{1,2})\s*-\s*(\d{1,2})\s*(am|pm)", v):
            off = 12 if ampm == "pm" else 0
            windows.append({"start": f"{int(a) % 12 + off:02d}:00", "end": f"{int(b) % 12 + off:02d}:00"})
        params["windows"] = windows
        if "weekday" in v:
            params["days"] = "weekdays"
    elif key == "detour":
        m = re.search(r"\+\s*(\d+)\s*%\s*distance", v)
        if m:
            params["extra_distance_pct"] = int(m.group(1))
        params["refusal_premium"] = "refusal premium" in v
    elif key == "student":
        m = re.search(r"(\d+)\s*%\s*off", v)
        if m:
            params["discount_pct"] = int(m.group(1))
        if "bus only" in v:
            params["applies_to_mode_codes"] = ["LOCAL_BUS"]
        m = re.search(rf"min(?:imum)?\s*(?:tk\.?|bdt)?\s*{_NUM}", v)
        if m:
            params["min_fare_bdt"] = float(m.group(1))
    elif key == "metro":
        if "matrix" in v:
            params["fare_source"] = "official_fare_matrix"
        if "unaffected" in v:
            params["affected_by_weather"] = False
            params["affected_by_blockage"] = False
    elif key == "disclaimer":
        params["non_metro_prices_are_estimates"] = "estimate" in v
    m = DECLARED_ALIAS_RE.search(value)
    if m:  # e.g. "Metro mapping: Uttara=Uttara North, Mirpur=Mirpur 10."
        pairs = re.findall(r"([^,;=]+?)\s*=\s*([^,;=]+?)\s*(?:[,;]|\.?\s*$)", m.group(1))
        params["declared_aliases"] = [{"alias": clean_name(a), "canonical": clean_name(b)} for a, b in pairs]
    return params


def _parse_rules(rows, src) -> list[dict]:
    out, notes = [], 0
    for i, row in enumerate(rows):
        cells = dl.non_empty(row)
        if not cells:
            continue
        if len(cells) == 2 and cells[0].lower() in ("rule", "rules", "key", "name", "parameter") \
                and cells[1].lower() in ("value", "description", "rule", "details"):
            continue
        text = ", ".join(cells)
        if "=" in cells[0]:
            m = RULE_RE.match(text)
            label, value = (clean_name(m["key"]), clean_name(m["value"])) if m else (None, text)
        elif len(cells) >= 2:
            label, value = cells[0], ", ".join(cells[1:])
        else:
            label, value = None, text
        if label:
            key = dl.normalize_header(label)
        elif "estimate" in value.lower() or "not official" in value.lower():
            key, label = "disclaimer", "Disclaimer"
        else:
            notes += 1
            key, label = f"note_{notes}", f"Note {notes}"
        out.append({"key": key, "label": label, "value": value, "params": rule_params(key, value),
                    **src, "source_row": i})
    return out


SECTION_PARSERS = {"wealth": _parse_wealth, "mode_rates": _parse_mode_rates,
                   "multipliers": _parse_multipliers, "rules": _parse_rules}


def parse_rules_grid(rows, sheet: str | None, src: dict) -> dict[str, list[dict]]:
    """Grid (CSV rows or one sheet) -> {wealth, mode_rates, multipliers, rules} raw records."""
    out = {k: [] for k in SECTION_PARSERS}
    for sec in dl.rule_sections(rows, sheet):
        if sec["kind"]:
            out[sec["kind"]] += SECTION_PARSERS[sec["kind"]](sec["rows"], src)
        else:  # untitled rows: accept only lines that match an unambiguous text pattern
            for row in sec["rows"]:
                text = ", ".join(dl.non_empty(row))
                if MODE_RATE_RE.search(text):
                    out["mode_rates"] += _parse_mode_rates([row], src)
                elif len(MULT_TERM_RE.findall(text)) >= 2:
                    out["multipliers"] += _parse_multipliers([row], src)
    return out


def problem_owner_grid() -> list[list[str]]:
    brief = dm.load_config("problem_owner_rules.json")
    rows: list[list[str]] = []
    for title, key in (("Hub wealth index", "hub_wealth_index"), ("Mode rates", "mode_rates"),
                       ("Multipliers", "multipliers"), ("Rules", "rules")):
        rows.append([title])
        rows += [[line] for line in brief.get(key, [])]
    return rows


# =============================================================================
# Co-ordination dataset
# =============================================================================
def _dedupe(coords):
    out = []
    for c in coords:
        if not out or out[-1] != c:
            out.append(c)
    return out


def _split_combined(text: str, separators) -> list[str] | None:
    for sep in separators:
        parts = [p.strip() for p in text.split(sep) if p.strip()]
        if len(parts) == 2:
            return parts
    return None


def parse_polyline_entry(entry: dict, mapping: dict, ctx: Context):
    grid = dl.load_grid(entry)
    file = entry["file_path"]
    layers_cfg = mapping.get("layers", {})
    order = mapping.get("coordinate_order", "lon_lat")
    inferred = (entry.get("layout") or {}).get("coordinate_order_inferred")
    if inferred in ("lon_lat", "lat_lon") and inferred != order:
        ctx.warn(f"{file}: configured coordinate order {order} contradicts data ({inferred}); using {inferred}")
        order = inferred
    separators = mapping.get("combined_stop_name_separators", ["."])
    road, transit, unknown_layers = [], [], set()
    for idx, row in enumerate(grid):
        if not dl.non_empty(row):
            continue
        row_no = idx + 1
        layer = str(row[0]).strip()
        cfg = layers_cfg.get(layer)
        if cfg is None and layer not in unknown_layers:
            unknown_layers.add(layer)
            ctx.warn(f"{file}: layer {layer!r} not in coordination_mapping.json; mode unknown")
        role = (cfg or {}).get("role")
        trailing = len(cfg.get("trailing_numeric_fields", [])) if role == "road_network" else (0 if cfg else None)
        p = dl.split_polyline_row(row, trailing)
        if p is None:
            ctx.warn(f"{file} row {row_no}: could not parse polyline row; skipped")
            continue
        coords = p["coords"] if order == "lon_lat" else [(y, x) for x, y in p["coords"]]
        coords = _dedupe(coords)
        if role == "road_network" or (role is None and not p["texts"]):
            names = (cfg or {}).get("trailing_numeric_fields") or [f"attr_{i + 1}_raw" for i in range(len(p["attrs"]))]
            road.append({
                "segment_id": f"ROAD_{row_no:06d}", "layer": layer,
                "coordinates": [[x, y] for x, y in coords],
                "length_km": round(dl.line_length_km(coords), 5),
                **{n: v for n, v in zip(names, p["attrs"])},
                "source_file": file, "source_row": row_no,
            })
            continue
        warnings, texts = [], p["texts"]
        if len(texts) == 1:
            parts = _split_combined(texts[0], separators)
            if not parts:
                ctx.warn(f"{file} row {row_no}: one stop name {texts[0]!r}; cannot tell from/to, skipped")
                continue
            warnings.append(f"single stop field {texts[0]!r} split into from={parts[0]!r}, to={parts[1]!r}")
            ctx.warn(f"{file} row {row_no}: {warnings[-1]}")
            texts = parts
        elif len(texts) > 2:
            warnings.append(f"extra trailing text fields ignored: {texts[1:-1]}")
        elif not texts:
            ctx.warn(f"{file} row {row_no}: transit row without stop names; skipped")
            continue
        transit.append({
            "layer": layer, "cfg": cfg or {}, "from_raw": clean_name(texts[0]), "to_raw": clean_name(texts[-1]),
            "coords": coords, "source_file": file, "source_row": row_no, "warnings": warnings,
        })
    return road, transit


def _shared_end(seg, other, tol) -> int | None:
    if other is None:
        return None
    ends = (other["coords"][0], other["coords"][-1])
    near = [min(dl.haversine_m(seg["coords"][k], e) for e in ends) <= tol for k in (0, -1)]
    if near[0] and not near[1]:
        return 0
    if near[1] and not near[0]:
        return 1
    return None


def _name_at(seg, end: int) -> str | None:
    if seg["orientation"] == "as_listed":
        return seg["from_raw"] if end == 0 else seg["to_raw"]
    if seg["orientation"] == "reversed":
        return seg["to_raw"] if end == 0 else seg["from_raw"]
    return None


def orient_transit(segs: list[dict], registry: HubRegistry, tol: float, ctx: Context) -> None:
    """Infer geometry orientation per row from endpoints shared with neighbouring rows,
    then check that both rows use the same stop name at each shared endpoint."""
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i > 0 and segs[i - 1]["layer"] == s["layer"] and segs[i - 1]["source_file"] == s["source_file"] else None
        nxt = segs[i + 1] if i + 1 < len(segs) and segs[i + 1]["layer"] == s["layer"] and segs[i + 1]["source_file"] == s["source_file"] else None
        s["prev"], s["shared_prev"], s["shared_next"] = prev, _shared_end(s, prev, tol), _shared_end(s, nxt, tol)
        votes = []
        if s["shared_prev"] is not None:
            votes.append("as_listed" if s["shared_prev"] == 0 else "reversed")
        if s["shared_next"] is not None:
            votes.append("as_listed" if s["shared_next"] == 1 else "reversed")
        s["orientation"] = (votes[0] if len(set(votes)) == 1 else "ambiguous") if votes else "unknown"
        s["end_status"] = {0: "orientation_inferred", 1: "orientation_inferred"}
        if s["orientation"] not in ("as_listed", "reversed"):
            s["end_status"] = {0: "orientation_unknown", 1: "orientation_unknown"}
    for s in segs:
        prev, k = s["prev"], s["shared_prev"]
        if prev is None or k is None:
            continue
        pk = 0 if dl.haversine_m(prev["coords"][0], s["coords"][k if k == 0 else -1]) <= tol else 1
        name_s, name_p = _name_at(s, k), _name_at(prev, pk)
        if name_s is None or name_p is None:
            continue
        if registry.same_hub(name_s, name_p):
            if s["end_status"][k] != "excluded_label_conflict":
                s["end_status"][k] = "junction_confirmed"
            if prev["end_status"][pk] != "excluded_label_conflict":
                prev["end_status"][pk] = "junction_confirmed"
            continue
        s["end_status"][k] = prev["end_status"][pk] = "excluded_label_conflict"
        point = s["coords"][0 if k == 0 else -1]
        conflict = {"source_file": s["source_file"], "layer": s["layer"],
                    "rows": [prev["source_row"], s["source_row"]], "names": [name_p, name_s],
                    "shared_point": list(point)}
        ctx.label_conflicts.append(conflict)
        registry.add_evidence(name_p, name_s, f"{s['source_file']} rows {prev['source_row']}/{s['source_row']} "
                                              f"end at the same point")


def parse_coord_table(entry: dict, ctx: Context, synonyms: dict) -> tuple[list[dict], list[dict]]:
    """Headered co-ordination table -> (edge rows, hub rows) with canonical field names."""
    table = dl.load_table(entry)
    ctx.column_mappings[entry["id"]] = table["raw_mapping"]
    cols = dl.map_columns(table["normalized_headers"], {**dl.COLUMN_SYNONYMS, **synonyms})
    edges, hubs = [], []
    for r in table["rows"]:
        rec = {field: r.get(header, "") for field, header in cols.items()}
        rec["_row_number"] = r["_row_number"]
        rec["_raw"] = {k: v for k, v in r.items() if not k.startswith("_")}
        if entry["detected_kind"] == dm.COORD_EDGES:
            edges.append(rec)
        else:
            hubs.append(rec)
    return edges, hubs


_TRUE = {"1", "true", "yes", "y", "t"}
_FALSE = {"0", "false", "no", "n", "f"}


def parse_direction(one_way, direction) -> dict:
    ow = str(one_way or "").strip().lower()
    d = re.sub(r"[\s-]+", "_", str(direction or "").strip().lower())
    if ow in _TRUE or d in ("one_way", "oneway", "forward", "reverse", "ft", "tf"):
        return {"one_way": True, "direction": "one_way", "direction_unknown": False}
    if ow in _FALSE or d in ("both", "bidirectional", "two_way", "2_way", "bi"):
        return {"one_way": False, "direction": "bidirectional", "direction_unknown": False}
    return {"one_way": None, "direction": None, "direction_unknown": True}


def parse_status(value) -> str:
    return "closed" if str(value or "").strip().lower() in ("closed", "blocked", "inactive", "suspended") else "active"


def parse_geometry(value):
    """WKT LINESTRING / GeoJSON string -> GeoJSON dict; anything else -> None (raw kept by caller)."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    m = re.match(r"^LINESTRING\s*\((.*)\)$", text, re.I)
    if m:
        pts = []
        for pair in m.group(1).split(","):
            xy = pair.split()
            if len(xy) < 2 or dl.strict_float(xy[0]) is None or dl.strict_float(xy[1]) is None:
                return None
            pts.append([float(xy[0]), float(xy[1])])
        return {"type": "LineString", "coordinates": pts}
    return None


# =============================================================================
# Edges
# =============================================================================
EDGE_FIELDS = [
    "route_id", "mode", "mode_code", "provider", "category", "line_name",
    "origin_hub_id", "destination_hub_id", "origin_name_raw", "destination_name_raw",
    "source_dataset", "source_file", "source_row", "source_layer", "source_route_id",
    "distance_km", "geometry_length_km", "time_normal_min", "time_peak_min", "time_rain_min",
    "one_way", "direction", "direction_unknown", "status", "status_raw",
    "geometry", "geometry_source", "geometry_orientation", "via_hub_ids", "fare_row_ids",
    "notes", "warnings", "is_test_fixture",
]


def _edge(**kw) -> dict:
    e = {f: None for f in EDGE_FIELDS}
    e.update(direction_unknown=True, status="active", via_hub_ids=[], fare_row_ids=[], warnings=[])
    e.update(kw)
    return e


def assign_route_ids(edges: list[dict], used: Counter) -> None:
    """{MODE}_{ORIGIN}_{DESTINATION}; duplicates get _2, _3 in the given (sorted) order."""
    for e in edges:
        base = f"{e['mode_code'] or 'UNKNOWN'}_{e['origin_hub_id'] or 'UNKNOWN'}_{e['destination_hub_id'] or 'UNKNOWN'}"
        used[base] += 1
        e["route_id"] = base if used[base] == 1 else f"{base}_{used[base]}"


# =============================================================================
# Hubs / coordinates
# =============================================================================
def pick_coordinate(observations: list[dict], radius_m: float) -> tuple[dict | None, str, list[dict]]:
    """Choose an OBSERVED point (medoid of the majority cluster). Never averages or invents."""
    if not observations:
        return None, "missing", []
    clusters: list[list[dict]] = []
    for ob in observations:
        for c in clusters:
            if dl.haversine_m(ob["point"], c[0]["point"]) <= radius_m:
                c.append(ob)
                break
        else:
            clusters.append([ob])
    clusters.sort(key=len, reverse=True)
    best = clusters[0]
    if len(clusters) > 1 and (len(best) == len(clusters[1]) or len(best) * 2 <= len(observations)):
        return None, "conflict", observations
    medoid = min(best, key=lambda o: sum(dl.haversine_m(o["point"], q["point"]) for q in best))
    status = "derived_from_route_geometry" if len(clusters) == 1 else "majority_of_route_geometry_endpoints"
    return medoid, status, (observations if len(clusters) > 1 else [])


# =============================================================================
# Main entry point
# =============================================================================
def normalize(manifest: dict, out_dir: Path | None = None, *, write: bool = True,
              problem_owner_fallback: bool | None = None) -> dict:
    source = manifest.get("source", "production")
    out_dir = Path(out_dir) if out_dir else dm.output_dir(source)
    if problem_owner_fallback is None:
        problem_owner_fallback = source == "production"
    ctx = Context(source)
    roots = {k: dm.resolve_path(v) for k, v in manifest.get("roots", {}).items()}
    override_dirs = [p for p in (roots.get("coordination"), roots.get("root")) if p]
    mapping = dm.load_config("coordination_mapping.json", override_dirs)
    modes = ModeResolver(dm.load_config("mode_aliases.json", override_dirs))
    brief = dm.load_config("problem_owner_rules.json")
    registry = HubRegistry(load_alias_config(), extra_preferred=brief.get("metro_stations", []))
    tol = float(mapping.get("junction_tolerance_m", 15))
    radius = float(mapping.get("coordinate_cluster_radius_m", 300))

    entries_by_kind: dict[str, list[dict]] = defaultdict(list)
    for e in manifest.get("datasets", []):
        if e.get("is_test_fixture") and source == "production":
            ctx.warn(f"skipped test fixture in production run: {e['file_path']}")
            continue
        if e["detected_kind"] == dm.UNKNOWN:
            ctx.warn(f"ignored unrecognised file: {e['id']}")
            continue
        entries_by_kind[e["detected_kind"]].append(e)

    # ---------------- parse: rules ----------------
    rule_sets = {k: [] for k in SECTION_PARSERS}
    rules_files = []
    for e in entries_by_kind[dm.SECTION_RULES]:
        src = {"source": "test_fixture" if e["is_test_fixture"] else "section_rules_file", "source_file": e["file_path"]}
        parsed = parse_rules_grid(dl.load_grid(e), e.get("sheet"), src)
        for k in rule_sets:
            rule_sets[k] += parsed[k]
        rules_files.append(e["id"])
    brief_sets = parse_rules_grid(problem_owner_grid(), None,
                                  {"source": "problem_owner_brief", "source_file": "dataset_config/problem_owner_rules.json"})
    rule_section_status = {}
    for k in rule_sets:
        if rule_sets[k]:
            rule_section_status[k] = "loaded_from_file"
        elif problem_owner_fallback:
            rule_sets[k] = brief_sets[k]
            rule_section_status[k] = "fallback_problem_owner_brief"
            if rules_files:
                ctx.warn(f"section rules file has no '{k}' section; using problem owner brief")
        else:
            rule_section_status[k] = "missing"
    if rules_files and source == "production":
        _cross_check(rule_sets, brief_sets, rule_section_status, modes, ctx)
    for r in rule_sets["rules"]:
        if r["source"] == "problem_owner_brief":
            continue
        for pair in r["params"].get("declared_aliases", []):
            before, target = registry.group_key(pair["alias"]), registry.group_key(pair["canonical"])
            if before not in (compact_key(pair["alias"]), target):
                ctx.warn(f"dataset alias {pair} overrides an earlier alias")
            registry.add_alias(pair["alias"], pair["canonical"], f"{r['source_file']} ({r['label']})")

    # ---------------- parse: metro ----------------
    metro_pairs = []
    for e in entries_by_kind[dm.METRO_FARE_MATRIX]:
        metro_pairs += [(*p, e) for p in parse_metro(e, ctx)]

    # ---------------- parse: fares ----------------
    fare_raw = []
    expected_fare_cols = {dl.normalize_header(c) for c in dm.EXPECTED_FARE_COLUMNS}
    for e in entries_by_kind[dm.TRANSPORT_FARES]:
        table = dl.load_table(e)
        ctx.column_mappings[e["id"]] = table["raw_mapping"]
        id_cols = [h for h in dl.sequential_id_columns(table) if h not in expected_fare_cols]
        for h in id_cols:
            ctx.warn(f"{e['file_path']}: column {table['raw_mapping'][h]!r} holds sequential row numbers; "
                     "kept as source_row_id, not as data")
        for r in table["rows"]:
            r["_row_id"] = r.pop(id_cols[0]) if id_cols else None
            for h in id_cols[1:]:
                r.pop(h, None)
            fare_raw.append((r, e))

    # ---------------- parse: co-ordination ----------------
    road_segments, transit = [], []
    for e in entries_by_kind[dm.COORD_POLYLINE]:
        r, t = parse_polyline_entry(e, mapping, ctx)
        road_segments += [{**x, "is_test_fixture": e["is_test_fixture"]} for x in r]
        transit += [{**x, "is_test_fixture": e["is_test_fixture"]} for x in t]
    table_edges, table_hubs = [], []
    for e in entries_by_kind[dm.COORD_EDGES] + entries_by_kind[dm.COORD_HUBS]:
        te, th = parse_coord_table(e, ctx, mapping.get("column_synonyms", {}))
        table_edges += [(x, e) for x in te]
        table_hubs += [(x, e) for x in th]

    # ---------------- register every hub name ----------------
    for s in transit:
        registry.observe(s["from_raw"], "co-ordination")
        registry.observe(s["to_raw"], "co-ordination")
    for r, _ in table_edges:
        registry.observe(r.get("from_hub"), "co-ordination")
        registry.observe(r.get("to_hub"), "co-ordination")
        for via in _split_list(r.get("stops")):
            registry.observe(via, "co-ordination")
    for r, _ in table_hubs:
        registry.observe(r.get("name") or r.get("hub_id"), "co-ordination")
    for r, _ in fare_raw:
        registry.observe(r.get("from_hub"), "dhaka_transport_fares")
        registry.observe(r.get("to_hub"), "dhaka_transport_fares")
        for station in _split_list(r.get("metro_stations")):
            registry.observe(station, "dhaka_transport_fares")
    for a, b, _, _, _ in metro_pairs:
        registry.observe(a, "dhaka_metro_fare_matrix")
        registry.observe(b, "dhaka_metro_fare_matrix")
    for w in rule_sets["wealth"]:
        registry.observe(w["hub_name"], "section_rules" if w["source"] != "problem_owner_brief" else "problem_owner_brief")

    orient_transit(transit, registry, tol, ctx)

    # ---------------- edges: co-ordination ----------------
    coord_edges: list[dict] = []
    for s in transit:
        cfg = s["cfg"]
        mode_code = cfg.get("mode_code") or "UNKNOWN"
        coord_edges.append(_edge(
            mode=cfg.get("mode"), mode_code=mode_code, provider=cfg.get("provider"),
            line_name=cfg.get("line_name"),
            origin_hub_id=registry.hub_id(s["from_raw"]), destination_hub_id=registry.hub_id(s["to_raw"]),
            origin_name_raw=s["from_raw"], destination_name_raw=s["to_raw"],
            source_dataset="co-ordination", source_file=s["source_file"], source_row=s["source_row"],
            source_layer=s["layer"], geometry_length_km=round(dl.line_length_km(s["coords"]), 4),
            geometry={"type": "LineString", "coordinates": [[x, y] for x, y in s["coords"]]},
            geometry_source="co-ordination", geometry_orientation=s["orientation"],
            warnings=list(s["warnings"]) + [
                f"stop name differs from neighbouring row at the {'origin' if _name_at(s, k) == s['from_raw'] else 'destination'} end"
                for k in (0, 1) if s["end_status"][k] == "excluded_label_conflict"],
            is_test_fixture=s["is_test_fixture"],
        ))
    for r, e in table_edges:
        if not clean_name(r.get("from_hub")) or not clean_name(r.get("to_hub")):
            ctx.warn(f"{e['file_path']} row {r['_row_number']}: edge without from/to; skipped")
            continue
        mode_code = modes.resolve(r.get("mode")) or (_fallback_mode_code(r.get("mode")) if r.get("mode") else "UNKNOWN")
        geometry = parse_geometry(r.get("geometry"))
        edge = _edge(
            mode=modes.mode_of(mode_code), mode_code=mode_code, provider=r.get("provider") or None,
            category=r.get("category") or None, line_name=r.get("line_name") or None,
            origin_hub_id=registry.hub_id(r["from_hub"]), destination_hub_id=registry.hub_id(r["to_hub"]),
            origin_name_raw=clean_name(r["from_hub"]), destination_name_raw=clean_name(r["to_hub"]),
            source_dataset="co-ordination", source_file=e["file_path"], source_row=r["_row_number"],
            source_route_id=r.get("route_id") or None,
            distance_km=dl.to_float(r.get("distance_km")), time_normal_min=dl.to_float(r.get("time_min")),
            status=parse_status(r.get("status")), status_raw=r.get("status") or None,
            geometry=geometry, geometry_source="co-ordination" if geometry else None,
            via_hub_ids=[registry.hub_id(v) for v in _split_list(r.get("stops"))],
            is_test_fixture=e["is_test_fixture"], **parse_direction(r.get("one_way"), r.get("direction")),
        )
        if r.get("geometry") and not geometry:
            edge["warnings"].append("geometry present but not WKT LINESTRING/GeoJSON; kept raw in geometry_raw")
            edge["geometry_raw"] = r["geometry"]
        if r.get("mode") and not modes.resolve(r.get("mode")):
            ctx.warn(f"{e['file_path']} row {r['_row_number']}: unmapped mode {r.get('mode')!r} -> {mode_code}")
        coord_edges.append(edge)
    coord_edges.sort(key=lambda x: (x["source_file"], x["source_row"]))
    used = Counter()
    assign_route_ids(coord_edges, used)

    # ---------------- fare rows (+ edges where co-ordination has none) ----------------
    index = defaultdict(list)
    for edge in coord_edges:
        index[(edge["mode_code"], edge["origin_hub_id"], edge["destination_hub_id"])].append(edge)
    fare_rows, fare_edges = [], []
    have_metro_matrix = bool(metro_pairs)
    for n, (r, e) in enumerate(sorted(fare_raw, key=lambda x: (x[1]["file_path"], x[0]["_row_number"])), start=1):
        rec = _fare_record(n, r, e, registry, modes, ctx)
        rec["superseded_by_metro_matrix"] = rec["mode_code"] == "MRT6" and have_metro_matrix
        key = (rec["mode_code"], rec["from_hub_id"], rec["to_hub_id"])
        match, how = (index.get(key) or [None])[0], "same_direction"
        if match is None:
            rev = [x for x in index.get((key[0], key[2], key[1]), []) if x["one_way"] is not True]
            match, how = (rev[0] if rev else None), "reverse_direction"
        if match is not None:
            match["fare_row_ids"].append(rec["fare_row_id"])
            rec["route_id"], rec["route_match"] = match["route_id"], how
        elif rec["from_hub_id"] and rec["to_hub_id"]:
            edge = _edge(
                mode=modes.mode_of(rec["mode_code"]) or rec["mode"], mode_code=rec["mode_code"],
                provider=rec["provider"], category=rec["category"],
                origin_hub_id=rec["from_hub_id"], destination_hub_id=rec["to_hub_id"],
                origin_name_raw=rec["from_hub_name"], destination_name_raw=rec["to_hub_name"],
                source_dataset="dhaka_transport_fares", source_file=e["file_path"], source_row=r["_row_number"],
                distance_km=rec["road_distance_km"], time_normal_min=rec["time_normal_min"],
                time_peak_min=rec["time_peak_hour_min"], time_rain_min=rec["time_rain_min"],
                fare_row_ids=[rec["fare_row_id"]], notes=rec["notes"], is_test_fixture=e["is_test_fixture"],
                **parse_direction(rec["raw"].get("one_way"), rec["raw"].get("direction")),
            )
            if rec["raw"].get("status"):
                edge["status"], edge["status_raw"] = parse_status(rec["raw"]["status"]), rec["raw"]["status"]
            fare_edges.append(edge)
            rec["route_match"] = "created_from_fare_row"
        fare_rows.append(rec)
    assign_route_ids(fare_edges, used)
    for edge in fare_edges:
        for rec in fare_rows:
            if rec["fare_row_id"] in edge["fare_row_ids"]:
                rec["route_id"] = edge["route_id"]
    route_edges = coord_edges + fare_edges

    # ---------------- metro fares ----------------
    metro_records, seen_pairs = [], {}
    student_metro = any(r["key"] == "student" and "metro" in r["value"].lower() and "bus only" not in r["value"].lower()
                        for r in rule_sets["rules"])
    for a, b, fare, row_no, e in metro_pairs:
        fa, fb = registry.hub_id(a), registry.hub_id(b)
        if fa == fb:
            continue
        seen_pairs[(fa, fb)] = fare
        metro_records.append({
            "from_station": clean_name(a), "to_station": clean_name(b),
            "from_hub_id": fa, "to_hub_id": fb, "fare_bdt": fare,
            "mode": "metro", "mode_code": "MRT6", "source": "Dhaka_Metro_Fare_Matrix",
            "source_file": e["file_path"], "source_row": row_no, "official": True,
            "affected_by_weather": False, "affected_by_blockage": False,
            "wealth_multiplier_applicable": False, "student_discount_applicable": student_metro,
            "is_test_fixture": e["is_test_fixture"],
        })
    for (fa, fb), fare in seen_pairs.items():
        back = seen_pairs.get((fb, fa))
        if back is not None and back != fare and fa < fb:
            ctx.warn(f"metro matrix asymmetric: {fa}->{fb} {fare} vs {fb}->{fa} {back}")
    # Metro precedence: the official matrix wins; flag fare rows that disagree with it.
    for rec in fare_rows:
        if rec["mode_code"] != "MRT6" or not seen_pairs:
            continue
        stations = rec["metro_station_hub_ids"] or [rec["from_hub_id"], rec["to_hub_id"]]
        official = seen_pairs.get((stations[0], stations[-1])) if len(stations) >= 2 else None
        rec["metro_matrix_fare_bdt"] = official
        normal = rec["fares"]["normal"]
        if official is None:
            ctx.warn(f"{rec['fare_row_id']}: metro row {stations} not found in official matrix")
        elif official not in (normal["low_bdt"], normal["high_bdt"]):
            ctx.warn(f"{rec['fare_row_id']}: metro fare {normal} differs from official matrix {official}")

    # ---------------- hubs ----------------
    observations: dict[str, list[dict]] = defaultdict(list)
    for s in transit:
        if s["orientation"] not in ("as_listed", "reversed"):
            continue
        for k in (0, 1):
            if s["end_status"][k] == "excluded_label_conflict":
                continue
            point = s["coords"][0 if k == 0 else -1]
            observations[registry.group_key(_name_at(s, k))].append({
                "point": point, "source_file": s["source_file"], "source_row": s["source_row"],
                "layer": s["layer"], "basis": s["end_status"][k]})
    explicit: dict[str, dict] = {}
    extra: dict[str, dict] = defaultdict(lambda: {"name_bn": None, "source_hub_ids": set()})
    for r, e in table_hubs:
        name = r.get("name") or r.get("hub_id")
        g = registry.group_key(name)
        if r.get("name_bn") and not extra[g]["name_bn"]:
            extra[g]["name_bn"] = r["name_bn"]
        if r.get("hub_id"):
            extra[g]["source_hub_ids"].add(r["hub_id"])
        lat, lng = dl.to_float(r.get("latitude")), dl.to_float(r.get("longitude"))
        if lat is None or lng is None:
            continue
        if g in explicit and dl.haversine_m(explicit[g]["point"], (lng, lat)) > radius:
            ctx.coordinate_conflicts.append({"hub_id": registry.group_hub_id(g), "kind": "explicit_rows_disagree",
                                             "points": [list(explicit[g]["point"]), [lng, lat]]})
        explicit.setdefault(g, {"point": (lng, lat), "source_file": e["file_path"], "source_row": r["_row_number"]})
    metro_groups = {registry.group_key(s["from_raw"]) for s in transit if s["cfg"].get("mode_code") == "MRT6"}
    metro_groups |= {registry.group_key(s["to_raw"]) for s in transit if s["cfg"].get("mode_code") == "MRT6"}
    metro_groups |= {registry.group_key(x) for a, b, *_ in metro_pairs for x in (a, b)}
    hubs = []
    for g in registry.groups():
        hub_id = registry.group_hub_id(g)
        if g in explicit:
            chosen, status, candidates = explicit[g], "from_dataset", []
        else:
            chosen, status, candidates = pick_coordinate(observations.get(g, []), radius)
            if status == "conflict" or candidates:
                ctx.coordinate_conflicts.append({
                    "hub_id": hub_id, "kind": status,
                    "points": [{"point": list(c["point"]), "source_file": c["source_file"],
                                "source_row": c["source_row"]} for c in candidates]})
        hubs.append({
            "hub_id": hub_id, "name": registry.group_display(g), "aliases": registry.aliases(g),
            "name_bn": extra[g]["name_bn"] if g in extra else None,
            "latitude": chosen["point"][1] if chosen else None,
            "longitude": chosen["point"][0] if chosen else None,
            "coordinate_status": status,
            "coordinate_source": ({"source_file": chosen["source_file"], "source_row": chosen["source_row"],
                                   **({"layer": chosen["layer"], "basis": chosen["basis"]} if "layer" in chosen else {})}
                                  if chosen else None),
            "coordinate_candidates": [{"longitude": c["point"][0], "latitude": c["point"][1],
                                       "source_file": c["source_file"], "source_row": c["source_row"]}
                                      for c in candidates],
            "source_datasets": registry.sources(g),
            "source_hub_ids": sorted(extra[g]["source_hub_ids"]) if g in extra else [],
            "is_metro_station": g in metro_groups,
            "is_test_fixture": source == "fixtures",
        })
    hubs.sort(key=lambda h: h["hub_id"])

    # ---------------- rules outputs ----------------
    mode_rates = []
    for r in rule_sets["mode_rates"]:
        code = modes.resolve(r["mode"]) or _fallback_mode_code(r["mode"])
        mode_rates.append({"mode": r["mode"], "mode_code": code, **{k: r[k] for k in (
            "base_fare_bdt", "per_km_bdt", "min_fare_bdt", "average_speed_kmh",
            "low_band_multiplier", "high_band_multiplier")},
            "source": r["source"], "source_file": r["source_file"], "raw_text": r["raw_text"]})
    multipliers = []
    for r in rule_sets["multipliers"]:
        code = modes.resolve(r["mode"]) or _fallback_mode_code(r["mode"])
        for scen in SCENARIOS:
            value = r["values"].get(scen)
            basis = "source"
            if value is None and scen == "normal":
                value, basis = 1.0, "implicit_baseline"
            elif value is None:
                basis = "not_specified_in_source"
            multipliers.append({"mode": r["mode"], "mode_code": code, "scenario": scen, "multiplier": value,
                                "basis": basis, "note": r.get("note"), "source": r["source"],
                                "source_file": r["source_file"]})
    wealth = [{"kind": "hub_wealth_index", "hub_name": w["hub_name"], "hub_id": registry.hub_id(w["hub_name"]),
               "wealth_index": w["wealth_index"], "note": w.get("note"), "source": w["source"],
               "source_file": w["source_file"]}
              for w in rule_sets["wealth"]]
    for rec in fare_rows:
        if rec["area_wealth_multiplier"] is not None or rec["wealth_index_from"] is not None:
            wealth.append({"kind": "fare_row_area_wealth_multiplier", "fare_row_id": rec["fare_row_id"],
                           "mode_code": rec["mode_code"], "from_hub_id": rec["from_hub_id"],
                           "to_hub_id": rec["to_hub_id"], "wealth_index_from": rec["wealth_index_from"],
                           "wealth_index_to": rec["wealth_index_to"],
                           "area_wealth_multiplier": rec["area_wealth_multiplier"],
                           "area_wealth_note": rec["area_wealth_note"], "source": "dhaka_transport_fares",
                           "source_file": rec["source_file"]})
    rules = [{k: r[k] for k in ("key", "label", "value", "params", "source", "source_file")} for r in rule_sets["rules"]]

    outputs = {
        "hubs": hubs, "route_edges": route_edges, "fare_rows": fare_rows, "metro_fares": metro_records,
        "mode_rates": mode_rates, "multipliers": multipliers, "wealth_indices": wealth, "rules": rules,
        "road_segments": road_segments,
    }

    # ---------------- report ----------------
    def group_status(kinds, loaded):
        files = [e["id"] for k in kinds for e in entries_by_kind[k]]
        return {"status": "loaded" if files and loaded else "missing", "files": files}

    datasets = {
        "coordination": {**group_status([dm.COORD_POLYLINE, dm.COORD_EDGES, dm.COORD_HUBS], True),
                         "transit_segments": len(transit), "road_segments": len(road_segments)},
        "dhaka_transport_fares": {**group_status([dm.TRANSPORT_FARES], True), "rows": len(fare_rows)},
        "dhaka_metro_fare_matrix": {**group_status([dm.METRO_FARE_MATRIX], True), "pairs": len(metro_records)},
        "section_rules": {**group_status([dm.SECTION_RULES], True), "sections": rule_section_status},
    }
    for name, info in datasets.items():
        if info["status"] == "missing":
            ctx.warn(f"{name}: no dataset file found -> its normalized output is empty"
                     + (" (section rules fall back to the problem owner brief)" if name == "section_rules"
                        and problem_owner_fallback else ""))
    contract_mode = all(info["status"] == "missing" for info in datasets.values())
    report = {
        "generated_at": dm.utc_now(),
        "source": source,
        "contains_test_data": source == "fixtures",
        "output_dir": dm.display_path(out_dir),
        "contract_mode": contract_mode,
        "production_data_available": source == "production" and not contract_mode,
        "message": ("production data unavailable: running in contract mode, outputs are empty or come only from "
                    "the problem owner brief" if contract_mode else "ok"),
        "datasets": datasets,
        "outputs": {OUTPUT_FILES[k]: len(v) for k, v in outputs.items()},
        "column_mappings": ctx.column_mappings,
        "hub_alias": {
            "applied_aliases": [{k: v for k, v in a.items()} for a in registry.applied_alias_entries()],
            "proposed_aliases": registry.proposals(),
        },
        "label_conflicts": ctx.label_conflicts,
        "coordinate_conflicts": ctx.coordinate_conflicts,
        "hubs_without_coordinates": [h["hub_id"] for h in hubs if h["latitude"] is None],
        "edges_direction_unknown": sum(1 for e in route_edges if e["direction_unknown"]),
        "assumptions": ASSUMPTIONS,
        "warnings": ctx.warnings,
    }
    if write:
        for key, records in outputs.items():
            dm.write_json(out_dir / OUTPUT_FILES[key], records, compact=key == "road_segments")
        dm.write_json(out_dir / REPORT_FILE, report)
    report["_outputs"] = outputs  # in-memory only (not written), convenient for callers/tests
    return report


def _split_list(value) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [clean_name(p) for p in re.split(r"\s*-+>\s*|[;|>→]|,(?!\d)", text) if clean_name(p)]


def _cross_check(rule_sets, brief_sets, status, modes, ctx) -> None:
    """Warn when a rules file disagrees with the problem owner brief."""
    if status.get("mode_rates") == "loaded_from_file":
        brief = {modes.resolve(r["mode"]): r for r in brief_sets["mode_rates"]}
        for r in rule_sets["mode_rates"]:
            b = brief.get(modes.resolve(r["mode"]))
            if not b:
                continue
            for k in ("base_fare_bdt", "per_km_bdt", "min_fare_bdt", "average_speed_kmh",
                      "low_band_multiplier", "high_band_multiplier"):
                if r[k] is not None and b[k] is not None and abs(r[k] - b[k]) > 1e-9:
                    ctx.warn(f"mode rate mismatch vs problem owner brief: {r['mode']} {k} {r[k]} != {b[k]}")
    if status.get("multipliers") == "loaded_from_file":
        brief = {modes.resolve(r["mode"]): r["values"] for r in brief_sets["multipliers"]}
        for r in rule_sets["multipliers"]:
            b = brief.get(modes.resolve(r["mode"])) or {}
            for scen, v in r["values"].items():
                if scen in b and v is not None and abs(v - b[scen]) > 1e-9:
                    ctx.warn(f"multiplier mismatch vs problem owner brief: {r['mode']} {scen} {v} != {b[scen]}")


def parse_metro(entry: dict, ctx: Context) -> list[tuple]:
    """Metro matrix (wide or long form) -> [(from_station, to_station, fare_bdt, row_number)]."""
    grid = dl.load_grid(entry)
    hi = dl.find_header_row(grid)
    if hi is None:
        ctx.warn(f"{entry['file_path']}: metro matrix has no header row")
        return []
    header = grid[hi]
    norm, mapping = dl.normalize_headers(header)
    ctx.column_mappings[entry["id"]] = mapping
    cols = dl.map_columns(norm)
    fare_col = next((i for i, h in enumerate(norm) if "fare" in h or "bdt" in h), None)
    pairs = []
    if {"from_hub", "to_hub"} <= set(cols) and fare_col is not None:
        fi, ti = norm.index(cols["from_hub"]), norm.index(cols["to_hub"])
        for offset, r in enumerate(grid[hi + 1:]):
            fare = dl.to_float(_cell(r, fare_col))
            if clean_name(_cell(r, fi)) and clean_name(_cell(r, ti)) and fare is not None:
                pairs.append((clean_name(r[fi]), clean_name(r[ti]), fare, hi + offset + 2))
        return pairs
    columns = [clean_name(c) for c in header[1:]]
    for offset, r in enumerate(grid[hi + 1:]):
        station = clean_name(r[0]) if r else ""
        if not station:
            continue
        for j, to_station in enumerate(columns):
            raw = _cell(r, j + 1)
            fare = dl.to_float(raw)
            if not to_station or fare is None:
                if raw and raw not in ("-", "—"):
                    ctx.warn(f"{entry['file_path']} row {hi + offset + 2}: non-numeric fare {raw!r}")
                continue
            pairs.append((station, to_station, fare, hi + offset + 2))
    return pairs


_FARE_NUMERIC = re.compile(r"(_bdt|_km|_min)$|^wealth_index|^area_wealth_multiplier$")


def _fare_record(n: int, r: dict, e: dict, registry: HubRegistry, modes: ModeResolver, ctx: Context) -> dict:
    raw = {}
    for k, v in r.items():
        if k.startswith("_"):
            continue
        v = v if v != "" else None
        raw[k] = dl.to_float(v) if v is not None and _FARE_NUMERIC.search(k) else v
        if v is not None and raw[k] is None:
            raw[k] = v  # keep unparseable numeric text as-is rather than dropping it
            ctx.warn(f"{e['file_path']} row {r['_row_number']}: {k}={v!r} is not numeric")
    mode = raw.get("mode")
    code = modes.resolve(mode, f"{raw.get('provider') or ''} {mode or ''}",
                         f"{raw.get('provider') or ''} {raw.get('category') or ''}", f"{mode or ''} {raw.get('category') or ''}")
    if code is None:
        code = _fallback_mode_code(mode)
        ctx.warn(f"{e['file_path']} row {r['_row_number']}: unmapped mode {mode!r} -> {code}")
    fares = {}
    for scen, prefix in FARE_SCENARIO_PREFIX.items():
        low, high = raw.get(f"{prefix}_low_bdt"), raw.get(f"{prefix}_high_bdt")
        fares[scen] = {"low_bdt": low if isinstance(low, float) else None,
                       "high_bdt": high if isinstance(high, float) else None}
    stations = _split_list(raw.get("metro_stations"))
    return {
        "fare_row_id": f"FR_{n:04d}", "source_file": e["file_path"], "source_row": r["_row_number"],
        "source_row_id": r.get("_row_id"),
        "mode": mode, "mode_code": code, "category": raw.get("category"), "provider": raw.get("provider"),
        "from_hub_name": clean_name(raw.get("from_hub")) or None, "to_hub_name": clean_name(raw.get("to_hub")) or None,
        "from_hub_id": registry.hub_id(raw.get("from_hub")), "to_hub_id": registry.hub_id(raw.get("to_hub")),
        "road_distance_km": _num(raw.get("road_distance_km")),
        "time_normal_min": _num(raw.get("time_normal_min")),
        "time_peak_hour_min": _num(raw.get("time_peak_hour_min")),
        "time_rain_min": _num(raw.get("time_rain_min")),
        "metro_stations": stations, "metro_station_hub_ids": [registry.hub_id(s) for s in stations],
        "wealth_index_from": _num(raw.get("wealth_index_from")), "wealth_index_to": _num(raw.get("wealth_index_to")),
        "area_wealth_multiplier": _num(raw.get("area_wealth_multiplier")),
        "area_wealth_note": raw.get("area_wealth_note"),
        "fares": fares, "student_rule": raw.get("student_rule"), "notes": raw.get("notes"),
        "route_id": None, "route_match": None, "is_test_fixture": e["is_test_fixture"], "raw": raw,
    }


def _num(v):
    return v if isinstance(v, float) else None
