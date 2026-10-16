"""Idempotent database seeding from normalized dataset artifacts.

Sources:
  normalized -> backend/data/generated/normalized_*.json  (missing files => empty, never faked)
  fixtures   -> backend/tests/fixtures/mini_datasets/     (normalized in memory, test-only)

Re-running a seed never duplicates rows. Rules for existing rows:
  * dataset tables (hubs, route_edges, fare rows, metro matrix, mode rates, multipliers, wealth):
    dataset-derived columns are refreshed; stale rows are removed. Runtime state on route_edges
    (status, closed_reason, flood/construction flags) is preserved.
  * users, fare_rules, reports: inserted only if missing (never overwritten).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from app.enums import (
    Direction, HubType, MultiplierAppliesTo, ReportStatus, ReportType, RouteEdgeStatus, Severity,
    TransitMode, UserRole, UserStatus, values,
)
from app.models import (
    FareMatrixRow, FareRule, Hub, MetroFareMatrix, ModeRate, Report, RouteEdge, ScenarioMultiplier,
    User, WealthIndex, utcnow,
)
from app.security import hash_password
from app.services import alert_rules as rules
from app.services import dataset_manifest as dm

SOURCES = ("normalized", "fixtures")
DEMO_PASSWORD = "password123"
DEMO_USERS = [
    ("admin@dhakapath.local", "Admin", UserRole.admin),
    ("user1@dhakapath.local", "Demo User 1", UserRole.user),
    ("user2@dhakapath.local", "Demo User 2", UserRole.user),
    ("user3@dhakapath.local", "Demo User 3", UserRole.user),
]

# Normalized `mode_code` (dataset_config/mode_aliases.json) -> canonical TransitMode.
MODE_CODE_TO_MODE = {
    "LOCAL_BUS": TransitMode.local_bus, "MRT6": TransitMode.mrt, "RICKSHAW": TransitMode.rickshaw,
    "CNG": TransitMode.cng, "CAR": TransitMode.car, "MOTORBIKE": TransitMode.motorbike,
    "PATHAO_BIKE": TransitMode.pathao_bike, "PATHAO_CAR": TransitMode.pathao_car,
    "UBER_CAR": TransitMode.uber_car, "UBER_MOTO": TransitMode.uber_moto,
}

# Student rule from the Section rules ("50% off bus only, min Tk 10"). Used only when the
# normalized rules contain no parsable student rule.
FALLBACK_STUDENT_RULE = {"discount_pct": 50, "applies_to_mode_codes": ["LOCAL_BUS"], "min_fare_bdt": 10.0}

NORMALIZED_FILES = {
    "hubs": "normalized_hubs.json", "route_edges": "normalized_route_edges.json",
    "fare_rows": "normalized_fare_rows.json", "metro_fares": "normalized_metro_fare_matrix.json",
    "mode_rates": "normalized_mode_rates.json", "multipliers": "normalized_multipliers.json",
    "wealth_indices": "normalized_wealth_indices.json", "rules": "normalized_rules.json",
}
REPORTS_FILES = {"normalized": "normalized_reports.json", "fixtures": "reports_TEST_FIXTURE.json"}


@dataclass
class SeedData:
    source: str
    tables: dict[str, list[dict]] = field(default_factory=lambda: {k: [] for k in NORMALIZED_FILES})
    reports: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)

    @property
    def is_fixture(self) -> bool:
        return self.source == "fixtures"

    @property
    def has_transit(self) -> bool:
        return bool(self.tables["hubs"])


@dataclass
class SeedResult:
    source: str
    counts: dict[str, dict[str, int]] = field(default_factory=dict)  # table -> inserted/updated/removed
    totals: dict[str, int] = field(default_factory=dict)             # table -> rows now in the DB
    warnings: list[str] = field(default_factory=list)

    def bump(self, table: str, kind: str, n: int = 1) -> None:
        self.counts.setdefault(table, {"inserted": 0, "updated": 0, "removed": 0})[kind] += n


# ----------------------------------------------------------------------------- loading
def load_seed_data(source: str, normalized_dir: Path | None = None) -> SeedData:
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    data = SeedData(source)
    if source == "fixtures":
        from app.services.dataset_loader import build_manifest
        from app.services.dataset_normalizer import normalize

        outputs = normalize(build_manifest("fixtures"), write=False)["_outputs"]
        for key in NORMALIZED_FILES:
            data.tables[key] = outputs.get(key, [])
        reports_path = dm.FIXTURE_ROOT / REPORTS_FILES["fixtures"]
    else:
        directory = Path(normalized_dir) if normalized_dir else dm.GENERATED_DIR
        for key, filename in NORMALIZED_FILES.items():
            path = directory / filename
            if not path.exists():
                data.missing_files.append(filename)
                continue
            try:
                data.tables[key] = dm.read_json(path)
            except ValueError as exc:
                data.warnings.append(f"{filename} is not valid JSON ({exc}); treated as empty")
        if data.missing_files:
            data.warnings.append(
                f"normalized files missing: {', '.join(data.missing_files)}. Run "
                "`python -m app.scripts.inspect_datasets && python -m app.scripts.normalize_datasets`. "
                "Seeding without them (no transit data is invented).")
        if any(r.get("is_test_fixture") for r in data.tables["hubs"] + data.tables["route_edges"]):
            raise ValueError("normalized files contain test-fixture records; refusing to seed them as production")
        reports_path = directory / REPORTS_FILES["normalized"]
    if reports_path.exists():
        try:
            payload = dm.read_json(reports_path)
            data.reports = payload.get("reports", []) if isinstance(payload, dict) else payload
        except ValueError as exc:
            data.warnings.append(f"{reports_path.name} is not valid JSON ({exc}); no reports seeded")
    return data


# ----------------------------------------------------------------------------- helpers
def _upsert(session: Session, model, pk, values_: dict, result: SeedResult, table: str,
            preserve: tuple[str, ...] = ()) -> Any:
    """Insert, or update dataset-derived columns of the existing row (columns in `preserve` are kept)."""
    row = session.get(model, pk)
    if row is None:
        row = model(**values_)
        session.add(row)
        result.bump(table, "inserted")
        return row
    changed = False
    for key, value in values_.items():
        if key in preserve or key == "created_at":
            continue
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    if changed:
        if hasattr(row, "updated_at"):
            row.updated_at = utcnow()
        session.add(row)
        result.bump(table, "updated")
    return row


def _remove_stale(session: Session, model, pk_attr: str, keep: set, result: SeedResult, table: str,
                  protected: set | None = None) -> None:
    protected = protected or set()
    for row in session.exec(select(model)).all():
        pk = getattr(row, pk_attr)
        if pk in keep:
            continue
        if pk in protected:
            result.warnings.append(f"{table}: kept stale row {pk!r} (still referenced)")
            continue
        session.delete(row)
        result.bump(table, "removed")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _mode(code: str | None) -> str | None:
    m = MODE_CODE_TO_MODE.get(code or "")
    return m.value if m else None


def _direction(edge: dict) -> str:
    if edge.get("direction_unknown") or edge.get("direction") is None and edge.get("one_way") is None:
        return Direction.unknown.value
    if edge.get("one_way") is True or edge.get("direction") == "one_way":
        return Direction.one_way.value
    if edge.get("one_way") is False or edge.get("direction") == "bidirectional":
        return Direction.bidirectional.value
    return Direction.unknown.value


def student_rule_params(rules: list[dict]) -> tuple[dict, str]:
    for r in rules:
        if r.get("key") == "student" and r.get("params", {}).get("discount_pct") is not None:
            return r["params"], f"rules:{r.get('source_file')}"
    return FALLBACK_STUDENT_RULE, "section rules default (problem owner brief)"


def build_fare_rules(data: SeedData) -> list[dict]:
    """One editable rule per TransitMode, from the student rule and fare-row notes."""
    params, _ = student_rule_params(data.tables["rules"])
    student_modes = {m for m in (_mode(c) for c in params.get("applies_to_mode_codes", [])) if m}
    surge_modes = {
        _mode(r.get("mode_code")) for r in data.tables["fare_rows"]
        if "surge" in str(r.get("notes") or "").lower()
    } - {None}
    out = []
    for mode in TransitMode:
        discount = float(params.get("discount_pct", 0)) if mode.value in student_modes else 0.0
        out.append({
            "mode": mode.value,
            "student_discount_percent": discount,
            "minimum_student_fare_bdt": params.get("min_fare_bdt") if discount else None,
            "surge_allowed": mode.value in surge_modes,
            "use_official_matrix": mode == TransitMode.mrt,
            "allow_formula_fallback": mode not in (TransitMode.mrt, TransitMode.walk),
        })
    return out


# ----------------------------------------------------------------------------- seeders
def seed_users(session: Session, result: SeedResult) -> dict[str, int]:
    for email, name, role in DEMO_USERS:
        if session.exec(select(User).where(User.email == email)).first():
            continue
        session.add(User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD),
                         role=role.value, is_verified=True, status=UserStatus.active.value))
        result.bump("users", "inserted")
    session.flush()
    return {u.email: u.id for u in session.exec(select(User)).all()}


def seed_fare_rules(session: Session, data: SeedData, result: SeedResult) -> None:
    for rule in build_fare_rules(data):
        if session.get(FareRule, rule["mode"]) is None:  # never overwrite admin edits
            session.add(FareRule(**rule))
            result.bump("fare_rules", "inserted")


def seed_transit(session: Session, data: SeedData, result: SeedResult) -> None:
    t = data.tables
    wealth_by_hub = {w["hub_id"]: w for w in t["wealth_indices"]
                     if w.get("kind") == "hub_wealth_index" and w.get("hub_id")}
    referenced_hubs = set(session.exec(select(Report.affected_hub).where(Report.affected_hub.is_not(None))))

    # hubs
    hub_ids: set[str] = set()
    for h in t["hubs"]:
        w = wealth_by_hub.get(h["hub_id"])
        _upsert(session, Hub, h["hub_id"], {
            "hub_id": h["hub_id"], "name": h["name"], "name_bn": h.get("name_bn"),
            "aliases_json": h.get("aliases", []), "latitude": h.get("latitude"), "longitude": h.get("longitude"),
            "type": (HubType.metro_station if h.get("is_metro_station") else HubType.hub).value,
            "wealth_index": w["wealth_index"] if w else None,
            "area_wealth_note": w.get("note") if w else None,
            "source_dataset": ",".join(h.get("source_datasets", [])), "raw_json": h,
        }, result, "hubs")
        hub_ids.add(h["hub_id"])
    session.flush()

    # route edges (runtime state is preserved)
    student_modes = {r["mode"] for r in build_fare_rules(data) if r["student_discount_percent"] > 0}
    edge_ids: set[str] = set()
    skipped = 0
    for e in t["route_edges"]:
        mode = _mode(e.get("mode_code"))
        if mode is None or e["origin_hub_id"] not in hub_ids or e["destination_hub_id"] not in hub_ids:
            skipped += 1
            continue
        status = e.get("status") if e.get("status") in values(RouteEdgeStatus) else RouteEdgeStatus.active.value
        _upsert(session, RouteEdge, e["route_id"], {
            "route_id": e["route_id"], "mode": mode, "provider": e.get("provider"), "category": e.get("category"),
            "origin_hub": e["origin_hub_id"], "destination_hub": e["destination_hub_id"],
            "direction": _direction(e), "status": status,
            "closed_reason": (f"dataset status: {e.get('status_raw')}" if status == "closed" else None),
            "road_distance_km": _num(e.get("distance_km")),
            "time_normal_min": _num(e.get("time_normal_min")), "time_peak_min": _num(e.get("time_peak_min")),
            "time_rain_min": _num(e.get("time_rain_min")),
            "geometry_json": e.get("geometry"), "stops_json": e.get("via_hub_ids") or None,
            "student_eligible": mode in student_modes,
            "source_dataset": e.get("source_dataset") or "", "raw_json": e,
        }, result, "route_edges", preserve=("status", "closed_reason"))
        edge_ids.add(e["route_id"])
    if skipped:
        result.warnings.append(f"route_edges: skipped {skipped} edge(s) with unmapped mode or unknown hub")
    session.flush()

    # fare rows (id = FR sequence number, so re-seeding updates in place)
    fare_ids: set[int] = set()
    for r in t["fare_rows"]:
        mode = _mode(r.get("mode_code"))
        if mode is None:
            result.warnings.append(f"fare_matrix_rows: skipped {r.get('fare_row_id')} (unmapped mode {r.get('mode')!r})")
            continue
        pk = int(re.sub(r"\D", "", r["fare_row_id"]))
        f = r["fares"]

        def rng(scen, side):
            return _num(f.get(scen, {}).get(f"{side}_bdt"))

        _upsert(session, FareMatrixRow, pk, {
            "id": pk, "mode": mode, "category": r.get("category"), "provider": r.get("provider"),
            "from_hub": r.get("from_hub_id"), "to_hub": r.get("to_hub_id"),
            "road_distance_km": r.get("road_distance_km"), "time_normal_min": r.get("time_normal_min"),
            "time_peak_hour_min": r.get("time_peak_hour_min"), "time_rain_min": r.get("time_rain_min"),
            "metro_stations": " -> ".join(r["metro_stations"]) if r.get("metro_stations") else None,
            "wealth_index_from": r.get("wealth_index_from"), "wealth_index_to": r.get("wealth_index_to"),
            "area_wealth_multiplier": r.get("area_wealth_multiplier"), "area_wealth_note": r.get("area_wealth_note"),
            "normal_low_bdt": rng("normal", "low"), "normal_high_bdt": rng("normal", "high"),
            "rain_low_bdt": rng("rain", "low"), "rain_high_bdt": rng("rain", "high"),
            "peak_office_hour_low_bdt": rng("peak", "low"), "peak_office_hour_high_bdt": rng("peak", "high"),
            "peak_plus_rain_low_bdt": rng("peak_plus_rain", "low"),
            "peak_plus_rain_high_bdt": rng("peak_plus_rain", "high"),
            "blockage_construction_waterlog_detour_low_bdt": rng("detour", "low"),
            "blockage_construction_waterlog_detour_high_bdt": rng("detour", "high"),
            "regular_holiday_low_bdt": rng("regular_holiday", "low"),
            "regular_holiday_high_bdt": rng("regular_holiday", "high"),
            "festive_holiday_low_bdt": rng("festive_holiday", "low"),
            "festive_holiday_high_bdt": rng("festive_holiday", "high"),
            "student_fare_low_bdt": rng("student", "low"), "student_fare_high_bdt": rng("student", "high"),
            "student_rule": r.get("student_rule"), "notes": r.get("notes"), "raw_json": r,
        }, result, "fare_matrix_rows")
        fare_ids.add(pk)

    # official metro matrix
    metro_keys: set[tuple[str, str]] = set()
    existing = {(m.from_station_hub_id, m.to_station_hub_id): m
                for m in session.exec(select(MetroFareMatrix)).all()}
    for m in t["metro_fares"]:
        key = (m["from_hub_id"], m["to_hub_id"])
        if key[0] not in hub_ids or key[1] not in hub_ids or key in metro_keys:
            continue
        metro_keys.add(key)
        vals = {"fare_bdt": m["fare_bdt"], "source_dataset": m.get("source", ""), "raw_json": m}
        row = existing.get(key)
        if row is None:
            session.add(MetroFareMatrix(from_station_hub_id=key[0], to_station_hub_id=key[1], **vals))
            result.bump("metro_fare_matrix", "inserted")
        elif any(getattr(row, k) != v for k, v in vals.items()):
            for k, v in vals.items():
                setattr(row, k, v)
            result.bump("metro_fare_matrix", "updated")
    for key, row in existing.items():
        if key not in metro_keys:
            session.delete(row)
            result.bump("metro_fare_matrix", "removed")

    # mode rates
    rate_modes: set[str] = set()
    for r in t["mode_rates"]:
        mode = _mode(r.get("mode_code"))
        if mode is None:
            result.warnings.append(f"mode_rates: skipped unmapped mode {r.get('mode')!r}")
            continue
        _upsert(session, ModeRate, mode, {
            "mode": mode, **{k: r.get(k) for k in (
                "base_fare_bdt", "per_km_bdt", "min_fare_bdt", "average_speed_kmh",
                "low_band_multiplier", "high_band_multiplier")}, "raw_json": r,
        }, result, "mode_rates")
        rate_modes.add(mode)

    # scenario multipliers (NULL multiplier = scenario not stated by the dataset)
    mult_keys: set[tuple[str, str]] = set()
    existing_m = {(m.mode, m.scenario): m for m in session.exec(select(ScenarioMultiplier)).all()}
    for m in t["multipliers"]:
        mode = _mode(m.get("mode_code"))
        if mode is None:
            continue
        key = (mode, m["scenario"])
        mult_keys.add(key)
        vals = {"multiplier": m.get("multiplier"), "applies_to": MultiplierAppliesTo.fare.value, "raw_json": m}
        row = existing_m.get(key)
        if row is None:
            session.add(ScenarioMultiplier(mode=mode, scenario=m["scenario"], **vals))
            result.bump("scenario_multipliers", "inserted")
        elif any(getattr(row, k) != v for k, v in vals.items()):
            for k, v in vals.items():
                setattr(row, k, v)
            result.bump("scenario_multipliers", "updated")
    for key, row in existing_m.items():
        if key not in mult_keys:
            session.delete(row)
            result.bump("scenario_multipliers", "removed")

    # wealth indices
    wealth_names: set[str] = set()
    for w in t["wealth_indices"]:
        if w.get("kind") != "hub_wealth_index":
            continue  # per-fare-row area multipliers already live in fare_matrix_rows
        hub_id = w.get("hub_id") if w.get("hub_id") in hub_ids else None
        _upsert(session, WealthIndex, w["hub_name"], {
            "hub_name": w["hub_name"], "hub_id": hub_id, "wealth_index": w["wealth_index"],
            "note": w.get("note"), "raw_json": w,
        }, result, "wealth_indices")
        wealth_names.add(w["hub_name"])
    session.flush()

    # stale cleanup, children before parents
    _remove_stale(session, WealthIndex, "hub_name", wealth_names, result, "wealth_indices")
    _remove_stale(session, ModeRate, "mode", rate_modes, result, "mode_rates")
    _remove_stale(session, FareMatrixRow, "id", fare_ids, result, "fare_matrix_rows")
    session.flush()
    _remove_stale(session, RouteEdge, "route_id", edge_ids, result, "route_edges")
    session.flush()
    _remove_stale(session, Hub, "hub_id", hub_ids, result, "hubs", protected=referenced_hubs)


def seed_reports(session: Session, data: SeedData, user_ids: dict[str, int], result: SeedResult) -> None:
    for r in data.reports:
        if session.get(Report, r["report_id"]):
            continue  # runtime state: never overwritten
        reporter = user_ids.get(r.get("reported_by_email", ""))
        if reporter is None or r.get("report_type") not in values(ReportType) \
                or r.get("severity") not in values(Severity) or r.get("status", "pending") not in values(ReportStatus):
            result.warnings.append(f"reports: skipped invalid report {r.get('report_id')!r}")
            continue
        hub = r.get("affected_hub")
        if hub and session.get(Hub, hub) is None:
            result.warnings.append(f"reports: {r['report_id']} references unknown hub {hub!r}; hub cleared")
            hub = None
        now = utcnow()
        report = Report(
            report_id=r["report_id"], report_type=r["report_type"], severity=r["severity"],
            affected_hub=hub, affected_route_ids_json=r.get("affected_route_ids", []),
            description=r["description"], mode=r.get("mode"), fare_amount_bdt=r.get("fare_amount_bdt"),
            reported_by=reporter, incident_time=now, created_at=now,
            half_life_min=int(r["half_life_min"]), expires_at=now + timedelta(minutes=int(r["expires_in_min"])),
            status=r.get("status", "pending"), admin_approved=bool(r.get("admin_approved", False)),
            raw_json=r,
        )
        rules.apply_decay(report, now)
        session.add(report)
        result.bump("reports", "inserted")


# ----------------------------------------------------------------------------- orchestration
def guard_source_mix(session: Session, data: SeedData) -> None:
    """Refuse to mix fixture and production transit data in one database."""
    for raw in session.exec(select(Hub.raw_json)).all():
        existing_is_fixture = bool((raw or {}).get("is_test_fixture"))
        if existing_is_fixture != data.is_fixture:
            have = "fixtures" if existing_is_fixture else "normalized"
            raise RuntimeError(f"database already holds '{have}' transit data; re-run with --reset "
                               f"to switch to '{data.source}'")
        return


def seed(session: Session, source: str, normalized_dir: Path | None = None) -> SeedResult:
    data = load_seed_data(source, normalized_dir)
    result = SeedResult(source, warnings=list(data.warnings))
    guard_source_mix(session, data)
    user_ids = seed_users(session, result)
    seed_transit(session, data, result)
    seed_fare_rules(session, data, result)
    session.flush()
    seed_reports(session, data, user_ids, result)
    session.commit()
    for model in (User, Hub, RouteEdge, FareMatrixRow, MetroFareMatrix, ModeRate, ScenarioMultiplier,
                  WealthIndex, FareRule, Report):
        result.totals[model.__tablename__] = session.exec(select(func.count()).select_from(model)).one()
    if not data.has_transit:
        result.warnings.append("no transit data seeded (hubs/route_edges/fares are empty); only users and "
                               "default fare_rules were created")
    return result


def format_result(result: SeedResult) -> str:
    lines = [f"Seed complete (source={result.source})"]
    for table, total in result.totals.items():
        c = result.counts.get(table, {})
        change = ", ".join(f"{k} {v}" for k, v in c.items() if v) or "no changes"
        lines.append(f"  {table:22} {total:6} rows  ({change})")
    lines += [f"WARNING: {w}" for w in result.warnings]
    return "\n".join(lines)
