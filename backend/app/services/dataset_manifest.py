"""Dataset kinds, expected columns, well-known paths and manifest read/write."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BACKEND_DIR / "dataset_config"
GENERATED_DIR = BACKEND_DIR / "data" / "generated"
FIXTURE_ROOT = BACKEND_DIR / "tests" / "fixtures" / "mini_datasets"

MANIFEST_FILE = "dataset_manifest.json"

# --- dataset kinds -----------------------------------------------------------
TRANSPORT_FARES = "dhaka_transport_fares"
METRO_FARE_MATRIX = "dhaka_metro_fare_matrix"
SECTION_RULES = "section_rules"
COORD_POLYLINE = "coordination_polyline"  # headerless: layer, lon, lat, ..., trailing fields
COORD_EDGES = "coordination_edges"        # headered table with from/to columns
COORD_HUBS = "coordination_hubs"          # headered table with name + lat/lng
UNKNOWN = "unknown"

# A "group" is one of the four production sources from docs/DATASET_CONTRACT.md.
DATASET_GROUPS: dict[str, list[str]] = {
    "dhaka_transport_fares": [TRANSPORT_FARES],
    "dhaka_metro_fare_matrix": [METRO_FARE_MATRIX],
    "section_rules": [SECTION_RULES],
    "coordination": [COORD_POLYLINE, COORD_EDGES, COORD_HUBS],
}

EXPECTED_FARE_COLUMNS = [
    "Mode", "Category", "Provider", "From_Hub", "To_Hub", "Road_Distance_km",
    "Time_Normal_min", "Time_Peak_Hour_min", "Time_Rain_min", "Metro_Stations",
    "Wealth_Index_From", "Wealth_Index_To", "Area_Wealth_Multiplier", "Area_Wealth_Note",
    "Normal_Low_BDT", "Normal_High_BDT", "Rain_Low_BDT", "Rain_High_BDT",
    "Peak_Office_Hour_Low_BDT", "Peak_Office_Hour_High_BDT",
    "Peak_Plus_Rain_Low_BDT", "Peak_Plus_Rain_High_BDT",
    "Blockage_Construction_Waterlog_Detour_Low_BDT", "Blockage_Construction_Waterlog_Detour_High_BDT",
    "Regular_Holiday_Low_BDT", "Regular_Holiday_High_BDT",
    "Festive_Holiday_Low_BDT", "Festive_Holiday_High_BDT",
    "Student_Fare_Low_BDT", "Student_Fare_High_BDT", "Student_Rule", "Notes",
]

REQUIRED_RULE_SECTIONS = ["wealth", "mode_rates", "multipliers", "rules"]

SOURCES = ("production", "fixtures")


def default_roots(source: str = "production") -> dict[str, Path]:
    """Where to look for datasets.

    root         -> files directly inside it (non-recursive): fare / metro / rules files
    datasets     -> optional folder, scanned recursively
    coordination -> official route & co-ordination dataset, scanned recursively
    """
    if source == "fixtures":
        return {"root": FIXTURE_ROOT, "datasets": FIXTURE_ROOT / "datasets",
                "coordination": FIXTURE_ROOT / "co-ordination"}
    if source != "production":
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    return {"root": BACKEND_DIR, "datasets": BACKEND_DIR / "datasets",
            "coordination": BACKEND_DIR / "co-ordination"}


def output_dir(source: str = "production") -> Path:
    # Fixture output is kept apart so test data can never overwrite production artifacts.
    return GENERATED_DIR / "fixtures" if source == "fixtures" else GENERATED_DIR


def manifest_path(source: str = "production") -> Path:
    return output_dir(source) / MANIFEST_FILE


def display_path(path: Path) -> str:
    """Path relative to backend/ when possible (posix), else absolute."""
    path = Path(path).resolve()
    try:
        return path.relative_to(BACKEND_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BACKEND_DIR / p


def is_fixture_path(path: Path) -> bool:
    p = Path(path).resolve()
    try:
        p.relative_to(FIXTURE_ROOT.resolve())
        return True
    except ValueError:
        return "TEST_FIXTURE" in p.name.upper()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_config(name: str, override_dirs: list[Path] | None = None) -> dict:
    """Load backend/dataset_config/<name>; shallow-merge dict keys from any override file."""
    base_path = CONFIG_DIR / name
    cfg: dict = read_json(base_path) if base_path.exists() else {}
    for d in override_dirs or []:
        p = Path(d) / name
        if p.exists():
            override = read_json(p)
            for key, value in override.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key] = {**cfg[key], **value}
                else:
                    cfg[key] = value
    return cfg
