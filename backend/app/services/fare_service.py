"""Fare engine. Reads rates, multipliers, fare rows and the metro matrix from the database.

Precedence (docs/FARE_ENGINE.md):
  walk   -> 0
  metro  -> metro_fare_matrix (exact; no weather/peak/detour/wealth/student adjustments)
  other  -> explicit fare_matrix_rows value for (mode, hub pair, scenario)
            else formula: mode_rates x scenario multiplier [x row's Area_Wealth_Multiplier], min fare
  student-> explicit Student_Fare columns (normal scenario) or fare_rules discount (default: bus 50%, min 10)

Money is kept at full precision; the schemas round to 2 decimals when serialized.
Every function that needs data takes the SQLModel `session` as its first argument.
Nothing is guessed: unknown distance / rates / metro pairs raise FareError.
"""
from __future__ import annotations

import math
from typing import Optional

from sqlmodel import Session, select

from app.models import FareMatrixRow, FareRule, Hub, MetroFareMatrix, ModeRate, RouteEdge, ScenarioMultiplier
from app.responses import ApiError
from app.schemas import (
    FareRange, FareSanityResult, LegFareResult, RideShareFareResult, TripFareResult,
)
from app.services.dataset_loader import haversine_m

ROAD_DISTANCE_FACTOR = 1.35  # Section rule: "Road distance = straight-line km x 1.35"
SANITY_MAX_RATIO = 5.0       # submitted fare > 5 x expected max => unrealistic
METRO = "mrt"
WALK = "walk"
SCENARIOS = ("normal", "rain", "peak", "peak_plus_rain", "detour", "regular_holiday", "festive_holiday")
# Simulation scenario names accepted as aliases.
SCENARIO_ALIASES = {"heavy_rain": "rain", "peak_rush_hour": "peak"}

# scenario -> FareMatrixRow column prefix (<prefix>_low_bdt / <prefix>_high_bdt)
EXPLICIT_COLUMNS = {
    "normal": "normal",
    "rain": "rain",
    "peak": "peak_office_hour",
    "peak_plus_rain": "peak_plus_rain",
    "detour": "blockage_construction_waterlog_detour",
    "regular_holiday": "regular_holiday",
    "festive_holiday": "festive_holiday",
    "student": "student_fare",
}
ESTIMATE_NOTE = "Non-metro prices are estimates for planning/demo, not official tariffs."


class FareError(ApiError):
    """Clear, non-guessing failure (e.g. DISTANCE_UNKNOWN)."""

    def __init__(self, code: str, message: str, status_code: int = 422, details: dict | None = None):
        super().__init__(code, message, status_code, details=details)


# ----------------------------------------------------------------------------- small helpers
def _ceil(x: float) -> int:
    """ceil that ignores float noise (30.000000000000004 -> 30)."""
    return math.ceil(round(x, 9))


def normalize_scenario(scenario: str | None) -> str:
    s = SCENARIO_ALIASES.get((scenario or "normal").strip().lower(), (scenario or "normal").strip().lower())
    if s not in SCENARIOS:
        raise FareError("SCENARIO_UNKNOWN", f"Unknown fare scenario {scenario!r}.",
                        details={"allowed": list(SCENARIOS)})
    return s


def make_range(low: float, high: float, source: str, notes: list[str] | None = None) -> FareRange:
    lo, hi = (low, high) if low <= high else (high, low)
    return FareRange(min_bdt=lo, max_bdt=hi, is_range=round(hi - lo, 9) > 0, source=source, notes=notes or [])


# ----------------------------------------------------------------------------- data accessors
def get_fare_rule(session: Session, mode: str) -> FareRule:
    rule = session.get(FareRule, mode)
    if rule is None:
        raise FareError("FARE_RULE_MISSING", f"No fare rule for mode {mode!r}. Run the seed script.", 404)
    return rule


def get_mode_rate(session: Session, mode: str) -> ModeRate:
    rate = session.get(ModeRate, mode)
    if rate is None or None in (rate.base_fare_bdt, rate.per_km_bdt, rate.min_fare_bdt,
                                rate.low_band_multiplier, rate.high_band_multiplier):
        raise FareError("MODE_RATE_MISSING", f"No complete mode rate for {mode!r}; cannot use the formula.", 404)
    return rate


def _scenario_multiplier(session: Session, mode: str, scenario: str) -> tuple[float, Optional[str]]:
    scenario = normalize_scenario(scenario)
    row = session.exec(select(ScenarioMultiplier).where(
        ScenarioMultiplier.mode == mode, ScenarioMultiplier.scenario == scenario)).first()
    if row is not None and row.multiplier is not None:
        return float(row.multiplier), None
    if scenario == "normal":
        return 1.0, None  # baseline
    if scenario == "peak_plus_rain":
        # The dataset states no combined multiplier; its explicit fare rows are ~ rain x peak.
        rain, _ = _scenario_multiplier(session, mode, "rain")
        peak, _ = _scenario_multiplier(session, mode, "peak")
        return rain * peak, "peak_plus_rain multiplier is not in the dataset; derived as rain x peak"
    raise FareError("SCENARIO_MULTIPLIER_UNKNOWN",
                    f"No multiplier for mode {mode!r}, scenario {scenario!r}.", 404)


def get_scenario_multiplier(session: Session, mode: str, scenario: str) -> float:
    return _scenario_multiplier(session, mode, scenario)[0]


def get_wealth_multiplier(fare_row: FareMatrixRow | None) -> float | None:
    """Explicit Area_Wealth_Multiplier of the matching fare row only; never derived from hub indices."""
    value = getattr(fare_row, "area_wealth_multiplier", None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ----------------------------------------------------------------------------- distance
def calculate_distance_km(edge: RouteEdge, origin_hub: Hub | None, destination_hub: Hub | None,
                          fallback_road_km: float | None = None) -> float:
    if edge.road_distance_km is not None:
        return float(edge.road_distance_km)
    if edge.straight_line_km is not None:
        return float(edge.straight_line_km) * ROAD_DISTANCE_FACTOR
    if fallback_road_km is not None:
        return float(fallback_road_km)
    if (origin_hub is not None and destination_hub is not None
            and None not in (origin_hub.latitude, origin_hub.longitude,
                             destination_hub.latitude, destination_hub.longitude)):
        meters = haversine_m((origin_hub.longitude, origin_hub.latitude),
                             (destination_hub.longitude, destination_hub.latitude))
        return meters / 1000 * ROAD_DISTANCE_FACTOR
    raise FareError("DISTANCE_UNKNOWN",
                    f"Distance for route {edge.route_id} is unknown (no road distance, straight-line "
                    "distance or hub coordinates); refusing to guess.",
                    details={"route_id": edge.route_id})


# ----------------------------------------------------------------------------- fare components
def calculate_normal_formula_fare(session: Session, mode: str, distance_km: float) -> FareRange:
    """Normal-scenario range: max(base + per_km x km, min) x low/high band."""
    rate = get_mode_rate(session, mode)
    normal = max(rate.base_fare_bdt + rate.per_km_bdt * distance_km, rate.min_fare_bdt)
    return make_range(normal * rate.low_band_multiplier, normal * rate.high_band_multiplier, "formula",
                      [ESTIMATE_NOTE])


def calculate_explicit_fare(fare_row: FareMatrixRow, scenario: str) -> FareRange | None:
    """Explicit low/high from the fare row, or None when the row has no value for the scenario."""
    scenario = scenario if scenario in EXPLICIT_COLUMNS else normalize_scenario(scenario)
    prefix = EXPLICIT_COLUMNS[scenario]
    low, high = getattr(fare_row, f"{prefix}_low_bdt"), getattr(fare_row, f"{prefix}_high_bdt")
    if low is None or high is None:
        return None
    notes = [] if fare_row.mode == METRO else [ESTIMATE_NOTE]
    return make_range(float(low), float(high), "explicit_fare_row", notes)


def apply_wealth_multiplier(fare_range: FareRange, wealth_multiplier: float | None, mode: str) -> FareRange:
    """Non-metro only, and only for an explicit numeric multiplier. Otherwise returned unchanged."""
    if (mode in (METRO, WALK) or wealth_multiplier is None or isinstance(wealth_multiplier, bool)
            or not isinstance(wealth_multiplier, (int, float)) or not math.isfinite(wealth_multiplier)):
        return fare_range
    out = make_range(fare_range.min_bdt * wealth_multiplier, fare_range.max_bdt * wealth_multiplier,
                     fare_range.source, list(fare_range.notes))
    out.notes.append(f"area wealth multiplier x{wealth_multiplier:g} applied")
    return out


def apply_minimum_fare(session: Session, fare_range: FareRange, mode: str) -> FareRange:
    minimum = get_mode_rate(session, mode).min_fare_bdt
    if fare_range.min_bdt >= minimum:
        return fare_range
    return make_range(max(fare_range.min_bdt, minimum), max(fare_range.max_bdt, minimum),
                      fare_range.source, fare_range.notes + [f"minimum fare {minimum:g} BDT applied"])


def _formula_fare(session: Session, mode: str, distance_km: float, scenario: str,
                  wealth_multiplier: float | None) -> FareRange:
    base = calculate_normal_formula_fare(session, mode, distance_km)
    multiplier, note = _scenario_multiplier(session, mode, scenario)
    scaled = make_range(base.min_bdt * multiplier, base.max_bdt * multiplier, "formula", list(base.notes))
    if scenario != "normal":
        scaled.notes.append(f"{scenario} multiplier x{multiplier:g}")
    if note:
        scaled.notes.append(note)
    scaled = apply_wealth_multiplier(scaled, wealth_multiplier, mode)
    return apply_minimum_fare(session, scaled, mode)


def lookup_metro_fare(session: Session, origin_hub_id: str, destination_hub_id: str) -> MetroFareMatrix | None:
    for a, b in ((origin_hub_id, destination_hub_id), (destination_hub_id, origin_hub_id)):
        row = session.exec(select(MetroFareMatrix).where(
            MetroFareMatrix.from_station_hub_id == a, MetroFareMatrix.to_station_hub_id == b)).first()
        if row is not None:
            return row
    return None


def calculate_metro_fare(session: Session, origin_hub_id: str, destination_hub_id: str) -> FareRange:
    """Official station-to-station fare. Use this (not a sum of segment fares) for a metro ride."""
    row = lookup_metro_fare(session, origin_hub_id, destination_hub_id)
    if row is None:
        raise FareError("METRO_FARE_UNKNOWN",
                        f"No official metro fare between {origin_hub_id} and {destination_hub_id}.", 404,
                        details={"origin_hub": origin_hub_id, "destination_hub": destination_hub_id})
    return make_range(row.fare_bdt, row.fare_bdt, "metro_matrix",
                      ["Official metro fare; unaffected by weather or blockage."])


def find_fare_row(session: Session, mode: str, origin_hub_id: str, destination_hub_id: str) -> FareMatrixRow | None:
    for a, b in ((origin_hub_id, destination_hub_id), (destination_hub_id, origin_hub_id)):
        row = session.exec(select(FareMatrixRow).where(
            FareMatrixRow.mode == mode, FareMatrixRow.from_hub == a, FareMatrixRow.to_hub == b,
        ).order_by(FareMatrixRow.id)).first()
        if row is not None:
            return row
    return None


def calculate_student_fare(session: Session, mode: str, regular: FareRange, scenario: str,
                           fare_row: FareMatrixRow | None) -> tuple[FareRange, bool]:
    """(student range, discount_applies). Discount comes from fare_rules (bus 50%, min 10 by default)."""
    rule = get_fare_rule(session, mode)
    if rule.student_discount_percent <= 0:
        return regular, False
    # Explicit Student_Fare columns are normal-scenario values.
    if scenario == "normal" and fare_row is not None:
        explicit = calculate_explicit_fare(fare_row, "student")
        if explicit is not None:
            return explicit, True
    keep = 1 - rule.student_discount_percent / 100
    floor = rule.minimum_student_fare_bdt or 0
    low = max(floor, _ceil(regular.min_bdt * keep))
    high = max(floor, _ceil(regular.max_bdt * keep))
    notes = list(regular.notes) + [f"student discount {rule.student_discount_percent:g}% (min {floor:g} BDT)"]
    return make_range(low, high, regular.source, notes), True


# ----------------------------------------------------------------------------- leg & trip
def calculate_leg_fare(session: Session, edge: RouteEdge, scenario: str = "normal",
                       prefer_student: bool = False) -> LegFareResult:
    """Fare for one route edge. With prefer_student=False the student fields mirror the regular fare."""
    scenario = normalize_scenario(scenario)
    mode = edge.mode
    warnings: list[str] = []
    distance: float | None = None
    fare_row: FareMatrixRow | None = None

    if mode == WALK:
        regular = make_range(0, 0, "walk", ["Walking is free."])
        student, applies = regular, False
    elif mode == METRO:
        regular = calculate_metro_fare(session, edge.origin_hub, edge.destination_hub)
        student, applies = regular, False
        if prefer_student:
            student, applies = calculate_student_fare(session, mode, regular, scenario, None)
        if scenario != "normal":
            warnings.append("Metro fare is unaffected by the scenario.")
    else:
        fare_row = find_fare_row(session, mode, edge.origin_hub, edge.destination_hub)
        regular = calculate_explicit_fare(fare_row, scenario) if fare_row else None
        if regular is not None:
            distance = edge.road_distance_km if edge.road_distance_km is not None else fare_row.road_distance_km
        else:
            rule = get_fare_rule(session, mode)
            if not rule.allow_formula_fallback:
                raise FareError("FORMULA_FALLBACK_DISABLED",
                                f"No explicit fare for {mode} {edge.origin_hub}->{edge.destination_hub} "
                                f"({scenario}) and formula fallback is disabled for this mode.", 404)
            distance = calculate_distance_km(edge, session.get(Hub, edge.origin_hub),
                                             session.get(Hub, edge.destination_hub),
                                             fare_row.road_distance_km if fare_row else None)
            regular = _formula_fare(session, mode, distance, scenario, get_wealth_multiplier(fare_row))
            warnings.append(
                f"No explicit {scenario} fare for this {mode} route; estimated with the formula." if fare_row is None
                else f"Fare row has no {scenario} value; estimated with the formula.")
            if fare_row is None:
                warnings.append("Formula fares carry no area wealth adjustment (no explicit multiplier).")
        student, applies = (calculate_student_fare(session, mode, regular, scenario, fare_row)
                            if prefer_student else (regular, False))

    return LegFareResult(
        route_id=edge.route_id, mode=mode,
        regular_fare_min_bdt=regular.min_bdt, regular_fare_max_bdt=regular.max_bdt,
        student_fare_min_bdt=student.min_bdt, student_fare_max_bdt=student.max_bdt,
        student_discount_applies=applies, scenario=scenario, distance_km=distance,
        fare_source=regular.source, warnings=warnings,
    )


def calculate_trip_fare(legs: list[LegFareResult], prefer_student: bool = False) -> TripFareResult:
    """Sum legs. Metro legs that are part of one ride should be priced once with calculate_metro_fare."""
    reg_min = sum(l.regular_fare_min_bdt for l in legs)
    reg_max = sum(l.regular_fare_max_bdt for l in legs)
    if prefer_student:
        stu_min = sum(l.student_fare_min_bdt for l in legs)
        stu_max = sum(l.student_fare_max_bdt for l in legs)
    else:
        stu_min, stu_max = reg_min, reg_max
    warnings = [f"{l.route_id}: {w}" for l in legs for w in l.warnings]
    if any(l.fare_source != "metro_matrix" and l.mode != WALK for l in legs):
        warnings.append(ESTIMATE_NOTE)
    return TripFareResult(
        total_regular_fare_min_bdt=reg_min, total_regular_fare_max_bdt=reg_max,
        total_student_fare_min_bdt=stu_min, total_student_fare_max_bdt=stu_max,
        legs=legs, warnings=warnings,
        student_discount_applies_any_leg=prefer_student and any(l.student_discount_applies for l in legs),
    )


# ----------------------------------------------------------------------------- ride-share
def calculate_rideshare_split(total_fare: FareRange, participants: int) -> RideShareFareResult:
    if not isinstance(participants, int) or isinstance(participants, bool) or participants < 1:
        raise FareError("INVALID_PARTICIPANTS", "participants must be an integer >= 1.", 422)
    per_min, per_max = total_fare.min_bdt / participants, total_fare.max_bdt / participants
    return RideShareFareResult(
        participants=participants,
        total_fare_min_bdt=total_fare.min_bdt, total_fare_max_bdt=total_fare.max_bdt,
        per_person_min_bdt=per_min, per_person_max_bdt=per_max,
        suggested_safe_per_person_bdt=_ceil(per_max),
    )


# ----------------------------------------------------------------------------- sanity check
SANITY_ABSOLUTE_MAX_BDT = 3000.0

def validate_submitted_fare(session: Session, mode: str, submitted_fare_bdt: float, distance_km: float | None,
                            scenario: str = "normal", origin_hub_id: str | None = None,
                            destination_hub_id: str | None = None) -> FareSanityResult:
    """Flag implausible user-submitted fares. Never blocks: callers set needs_admin_review."""
    scenario = normalize_scenario(scenario)
    result = FareSanityResult(mode=mode, submitted_fare_bdt=submitted_fare_bdt, checked=False,
                              is_realistic=True, needs_admin_review=False)

    def flag(reason: str) -> FareSanityResult:
        result.is_realistic, result.needs_admin_review = False, True
        result.reasons.append(reason)
        return result

    if submitted_fare_bdt < 0:
        result.checked = True
        return flag("Submitted fare is negative.")
    if mode == WALK:
        result.checked = True
        return flag("Walking has no fare.") if submitted_fare_bdt > 0 else result
    if mode == METRO:
        if origin_hub_id and destination_hub_id:
            row = lookup_metro_fare(session, origin_hub_id, destination_hub_id)
            if row is not None:
                result.checked = True
                result.expected = make_range(row.fare_bdt, row.fare_bdt, "metro_matrix")
                if abs(submitted_fare_bdt - row.fare_bdt) > 1e-9:
                    return flag(f"Official metro fare is {row.fare_bdt:g} BDT; submitted {submitted_fare_bdt:g}.")
                return result
        result.reasons.append("Metro stations unknown or not in the official matrix; could not verify.")
        return result
    if submitted_fare_bdt > SANITY_ABSOLUTE_MAX_BDT:  # no distance needed: no single Dhaka city ride costs this much
        result.checked = True
        return flag(f"Submitted {submitted_fare_bdt:g} BDT is above the {SANITY_ABSOLUTE_MAX_BDT:g} BDT ceiling for a "
                    f"single city ride.")
    if distance_km is None:
        result.reasons.append("Distance unknown; could not verify (DISTANCE_UNKNOWN).")
        return result
    try:
        expected = _formula_fare(session, mode, distance_km, scenario, None)
    except FareError as exc:
        result.reasons.append(f"Could not verify: {exc.code}.")
        return result
    result.checked, result.expected = True, expected
    if submitted_fare_bdt > SANITY_MAX_RATIO * expected.max_bdt:
        return flag(f"Submitted {submitted_fare_bdt:g} BDT is more than {SANITY_MAX_RATIO:g}x the expected "
                    f"maximum ({expected.max_bdt:.2f} BDT).")
    return result
