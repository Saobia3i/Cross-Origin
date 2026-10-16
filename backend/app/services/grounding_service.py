"""Grounding: check AI-generated / assistant-facing travel payloads against the database and the engines.

The model is never trusted for routes, fares, hubs, alerts or durations. A payload is *grounded* only if every
hub, route, leg connection, fare, student fare, duration, alert and mode it states matches what the database,
`fare_service` and `routing_service` compute. Anything that cannot be verified counts as a violation. When a
payload fails, callers discard it and use the deterministic engine output (`fallback_action`).

Every function takes the SQLModel `session` as its first argument.

Extra violation codes beyond the core list: UNKNOWN_MODE, ROUTE_NOT_AVAILABLE (route exists but is closed or not
traversable that way), ALERT_NOT_ACTIVE, NO_PAYLOAD.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.enums import TransitMode, values
from app.models import Hub, Report, RouteEdge, utcnow
from app.responses import api_error
from app.services import alert_rules
from app.services import fare_service as fs
from app.services import routing_service as rs
from app.services.fare_service import FareError

FARE_TOLERANCE_BDT = 0.5       # engine values are rounded to 2 dp; this absorbs display rounding only
DURATION_ABS_TOL_MIN = 2.0     # tolerance: max(2 min, 5%)
DURATION_REL_TOL = 0.05
WALK = "walk"
FAKE_FALLBACK = {"type": "use_route_engine_result",
                 "message": "Discard AI-generated route and use deterministic route engine output."}


class Violation(BaseModel):
    code: str
    message: str
    offending_value: Any = None
    field: Optional[str] = None


class GroundingResult(BaseModel):
    grounded: bool = True
    safe_for_display: bool = True
    violations: list[Violation] = Field(default_factory=list)
    checked_route_ids: list[str] = Field(default_factory=list)
    checked_hub_ids: list[str] = Field(default_factory=list)
    fare_check_passed: bool = True
    duration_check_passed: bool = True
    fallback_used: bool = False

    def badge(self) -> str:
        return "grounded" if self.grounded else "blocked"


def duration_ok(claimed: float, actual: float | None) -> bool:
    if actual is None:
        return False
    return abs(claimed - actual) <= max(DURATION_ABS_TOL_MIN, DURATION_REL_TOL * actual)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


class _Check:
    """Collects violations for one payload."""

    def __init__(self) -> None:
        self.violations: list[Violation] = []
        self.route_ids: list[str] = []
        self.hub_ids: list[str] = []

    def add(self, code: str, message: str, value: Any = None, field: str | None = None) -> None:
        self.violations.append(Violation(code=code, message=message, offending_value=value, field=field))

    def see_route(self, rid) -> None:
        if isinstance(rid, str) and rid not in self.route_ids:
            self.route_ids.append(rid)

    def see_hub(self, hid) -> None:
        if isinstance(hid, str) and hid not in self.hub_ids:
            self.hub_ids.append(hid)

    def result(self, fallback_used: bool = False) -> GroundingResult:
        codes = {v.code for v in self.violations}
        ok = not self.violations
        return GroundingResult(
            grounded=ok, safe_for_display=ok, violations=self.violations, checked_route_ids=self.route_ids,
            checked_hub_ids=self.hub_ids,
            fare_check_passed=not codes & {"FARE_MISMATCH", "INVALID_STUDENT_RULE"},
            duration_check_passed="DURATION_MISMATCH" not in codes, fallback_used=fallback_used)


# ============================================================================= hubs
def _resolve_hub_strict(session: Session, value) -> Hub | None:
    """A hub id, canonical name or alias; never a fuzzy guess."""
    if not isinstance(value, str) or not value.strip():
        return None
    r = rs.resolve_hub(session, value)
    return r.hub if r.hub is not None and r.matched_by in ("hub_id", "name", "alias") else None


def _check_hub_field(c: _Check, session: Session, value, field: str) -> str | None:
    if value is None:
        return None
    hub = _resolve_hub_strict(session, value)
    if hub is None:
        c.add("UNKNOWN_HUB", f"Hub '{value}' does not exist.", value, field)
        return None
    c.see_hub(hub.hub_id)
    return hub.hub_id


# ============================================================================= alerts
def _check_alerts(c: _Check, session: Session, payload: dict, legs: list[dict]) -> None:
    mentioned: list[tuple[str, Any, str]] = []  # (alert id, claimed dict or None, field)
    for key in ("alert_ids", "routing_alert_ids", "affecting_alert_ids"):
        for i, a in enumerate(payload.get(key) or []):
            mentioned.append((a, {"affects_routing": True} if key != "alert_ids" else None, f"{key}[{i}]"))
    for i, a in enumerate(payload.get("alerts") or []):
        if isinstance(a, dict):
            mentioned.append((a.get("alert_id") or a.get("report_id"), a, f"alerts[{i}]"))
        else:
            mentioned.append((a, None, f"alerts[{i}]"))
    for n, leg in enumerate(legs):
        for key in ("alert_ids", "routing_alert_ids"):
            for a in leg.get(key) or []:
                mentioned.append((a, {"affects_routing": True} if key == "routing_alert_ids" else None, f"legs[{n}].{key}"))
    now = utcnow()
    for alert_id, claim, field in mentioned:
        report = session.get(Report, alert_id) if isinstance(alert_id, str) else None
        if report is None:
            c.add("UNKNOWN_ALERT", f"Alert '{alert_id}' does not exist.", alert_id, field)
            continue
        claim = claim or {}
        if claim.get("status") is not None and claim["status"] != report.status:
            c.add("ALERT_NOT_ACTIVE", f"Alert {alert_id} is '{report.status}', not '{claim['status']}'.",
                  claim["status"], field)
        if claim.get("affects_routing") and not alert_rules.affects_routing(report, now):
            c.add("ALERT_NOT_ACTIVE", f"Alert {alert_id} ({report.status}) does not currently affect routing; only "
                  "verified, unexpired, confident alerts do.", alert_id, field)


# ============================================================================= fares & durations
def _range_ok(cmin, cmax, lo, hi) -> bool:
    return cmin >= lo - FARE_TOLERANCE_BDT and cmax <= hi + FARE_TOLERANCE_BDT and cmin <= cmax + 1e-9


def _check_fare_claim(c: _Check, claim_min, claim_max, ref_min: float, ref_max: float, what: str, field: str,
                      value, exact_metro: bool = False, student: bool = False) -> None:
    code = "INVALID_STUDENT_RULE" if student else "FARE_MISMATCH"
    cmin, cmax = _num(claim_min), _num(claim_max)
    if cmin is None and cmax is None:
        return
    cmin, cmax = (cmin if cmin is not None else cmax), (cmax if cmax is not None else cmin)
    if exact_metro:
        if abs(cmin - ref_min) > FARE_TOLERANCE_BDT or abs(cmax - ref_max) > FARE_TOLERANCE_BDT:
            c.add(code, f"{what}: the official metro fare is {ref_min:g} BDT.", value, field)
        return
    if not _range_ok(cmin, cmax, ref_min, ref_max):
        c.add(code, f"{what}: claimed {cmin:g}-{cmax:g} BDT is outside the calculated range "
              f"{ref_min:.2f}-{ref_max:.2f} BDT.", value, field)
    elif ref_max - ref_min > FARE_TOLERANCE_BDT and abs(cmax - cmin) <= FARE_TOLERANCE_BDT:
        c.add(code, f"{what}: a single exact fare ({cmin:g} BDT) was claimed but the source fare is a range "
              f"({ref_min:.2f}-{ref_max:.2f} BDT).", value, field)


def _check_duration_claim(c: _Check, claimed, actual: float | None, what: str, field: str) -> None:
    claimed = _num(claimed)
    if claimed is None:
        return
    if actual is None:
        c.add("DURATION_MISMATCH", f"{what}: the dataset has no travel time here, so a duration cannot be stated.",
              claimed, field)
    elif not duration_ok(claimed, actual):
        c.add("DURATION_MISMATCH", f"{what}: claimed {claimed:g} min but the engine calculates {actual:.1f} min "
              f"(allowed: +/-{max(DURATION_ABS_TOL_MIN, DURATION_REL_TOL * actual):.1f} min).", claimed, field)


# ============================================================================= core
def _legs_of(payload: dict) -> list[dict]:
    legs = payload.get("legs")
    if isinstance(legs, list) and legs:
        return [l if isinstance(l, dict) else {"route_id": l} for l in legs]
    return [{"route_id": r} for r in payload.get("route_ids") or []]


def _endpoints(c: _Check, session: Session, payload: dict, legs: list[dict]) -> tuple[str | None, str | None]:
    origin = _check_hub_field(c, session, _first(payload, "origin_hub", "origin"), "origin")
    destination = _check_hub_field(c, session, _first(payload, "destination_hub", "destination"), "destination")
    ids = payload.get("hub_ids") or []
    for i, h in enumerate(ids):
        _check_hub_field(c, session, h, f"hub_ids[{i}]")
    if origin is None and legs and legs[0].get("from_hub"):
        h = _resolve_hub_strict(session, legs[0]["from_hub"])
        origin = h.hub_id if h else None
    if destination is None and legs and legs[-1].get("to_hub"):
        h = _resolve_hub_strict(session, legs[-1]["to_hub"])
        destination = h.hub_id if h else None
    if origin is None and len(ids) >= 2:
        h = _resolve_hub_strict(session, ids[0])
        origin = h.hub_id if h else None
    if destination is None and len(ids) >= 2:
        h = _resolve_hub_strict(session, ids[-1])
        destination = h.hub_id if h else None
    return origin, destination


def _direct_edge_exists(session: Session, a: str, b: str) -> bool:
    return session.exec(select(RouteEdge).where(
        ((RouteEdge.origin_hub == a) & (RouteEdge.destination_hub == b))
        | ((RouteEdge.origin_hub == b) & (RouteEdge.destination_hub == a)))).first() is not None


def _validate(session: Session, payload: dict, *, scenario: str | None = None, session_id: str | None = None,
              graph: rs.Graph | None = None) -> _Check:
    c = _Check()
    if isinstance(payload, dict) and isinstance(payload.get("route_option"), dict):
        payload = payload["route_option"]
    if not isinstance(payload, dict) or not payload:
        c.add("NO_PAYLOAD", "There is no route payload to check.", None, None)
        return c
    try:
        scen = fs.normalize_scenario(payload.get("scenario") or scenario or "normal")
    except FareError as exc:
        c.add("FARE_MISMATCH", exc.message, payload.get("scenario"), "scenario")
        scen = "normal"
    graph = graph or rs.build_graph(session, session_id, scenario=scen)
    directed = {(ge.route_id, ge.from_hub, ge.to_hub): ge for ge in graph.edges}
    legs = _legs_of(payload)
    origin, destination = _endpoints(c, session, payload, legs)

    # ---- routes, modes, endpoints, availability
    path: list[rs.GraphEdge | None] = []
    resolved_ends: list[tuple[str | None, str | None]] = []
    for i, leg in enumerate(legs):
        f = f"legs[{i}]"
        rid = leg.get("route_id")
        c.see_route(rid)
        a = _check_hub_field(c, session, leg.get("from_hub"), f"{f}.from_hub")
        b = _check_hub_field(c, session, leg.get("to_hub"), f"{f}.to_hub")
        resolved_ends.append((a, b))
        edge = session.get(RouteEdge, rid) if isinstance(rid, str) else None
        if edge is None:
            c.add("UNKNOWN_ROUTE_ID", "Route does not exist in transit graph.", rid, f"{f}.route_id")
            if a and b and not _direct_edge_exists(session, a, b):
                c.add("HALLUCINATED_DIRECT_ROUTE", f"There is no direct route between {a} and {b}.", f"{a}->{b}", f)
            elif not (a and b) and len(legs) == 1 and len(payload.get("hub_ids") or []) >= 2:
                ha, hb = (_resolve_hub_strict(session, h) for h in (payload["hub_ids"][0], payload["hub_ids"][-1]))
                if ha and hb and not _direct_edge_exists(session, ha.hub_id, hb.hub_id):
                    c.add("HALLUCINATED_DIRECT_ROUTE", f"There is no direct route between {ha.hub_id} and {hb.hub_id}.",
                          f"{ha.hub_id}->{hb.hub_id}", f)
            path.append(None)
            continue
        mode = leg.get("mode")
        if mode is not None and (mode not in values(TransitMode) or mode != edge.mode):
            c.add("UNKNOWN_MODE", f"Mode '{mode}' is not valid for route {rid} (it is {edge.mode}).", mode, f"{f}.mode")
        names = {n.lower() for n in (edge.provider, (edge.raw_json or {}).get("line_name"), edge.route_id) if n}
        claimed_name = _first(leg, "line_name", "route_name", "bus_name", "provider")
        if claimed_name and str(claimed_name).lower() not in names:
            c.add("UNKNOWN_ROUTE_ID", f"'{claimed_name}' is not a known line for route {rid}.", claimed_name, f"{f}.line_name")
        if a and b and {a, b} != {edge.origin_hub, edge.destination_hub}:
            c.add("UNKNOWN_ROUTE_ID", f"Route {rid} connects {edge.origin_hub} and {edge.destination_hub}, not {a} and {b}.",
                  rid, f"{f}.route_id")
            path.append(None)
            continue
        if not (a and b):
            a, b = edge.origin_hub, edge.destination_hub  # unspecified direction: assume the route's own
            resolved_ends[-1] = (a, b)
        ge = directed.get((rid, a, b))
        if ge is None:
            why = graph.closed.get(rid) or "it cannot be travelled in that direction"
            c.add("ROUTE_NOT_AVAILABLE", f"Route {rid} is not available from {a} to {b}: {why}.", rid, f"{f}.route_id")
        path.append(ge)

    # ---- connectivity
    for i in range(len(legs) - 1):
        to_prev, from_next = resolved_ends[i][1], resolved_ends[i + 1][0]
        if to_prev and from_next and to_prev != from_next:
            c.add("DISCONNECTED_LEGS", f"Leg {i + 1} ends at {to_prev} but leg {i + 2} starts at {from_next}.",
                  [to_prev, from_next], f"legs[{i + 1}].from_hub")
    if legs:
        if origin and resolved_ends[0][0] and resolved_ends[0][0] != origin and _first(payload, "origin_hub", "origin"):
            c.add("DISCONNECTED_LEGS", f"The journey starts at {origin} but the first leg starts at {resolved_ends[0][0]}.",
                  resolved_ends[0][0], "legs[0].from_hub")
        if destination and resolved_ends[-1][1] and resolved_ends[-1][1] != destination and _first(payload, "destination_hub", "destination"):
            c.add("DISCONNECTED_LEGS", f"The journey ends at {destination} but the last leg ends at {resolved_ends[-1][1]}.",
                  resolved_ends[-1][1], f"legs[{len(legs) - 1}].to_hub")

    _check_alerts(c, session, payload, legs)
    _check_numbers(c, session, payload, legs, path, graph, scen, session_id, origin, destination)
    return c


def _check_numbers(c: _Check, session: Session, payload: dict, legs: list[dict], path: list, graph: rs.Graph,
                   scenario: str, session_id: str | None, origin: str | None, destination: str | None) -> None:
    claimed_scalar = _num(_first(payload, "claimed_fare_bdt", "total_fare_bdt"))
    claimed_dur = _first(payload, "claimed_duration_min", "total_duration_min")
    claimed_travel = payload.get("total_travel_time_min")
    has_total_claims = any(_num(payload.get(k)) is not None for k in (
        "total_regular_fare_min_bdt", "total_regular_fare_max_bdt", "total_student_fare_min_bdt", "total_student_fare_max_bdt"))
    leg_claims = any(_num(l.get(k)) is not None for l in legs for k in (
        "regular_fare_min_bdt", "regular_fare_max_bdt", "student_fare_min_bdt", "student_fare_max_bdt", "travel_time_min",
        "fare_bdt"))
    if claimed_scalar is None and claimed_dur is None and claimed_travel is None and not has_total_claims and not leg_claims:
        return

    complete = bool(legs) and all(p is not None for p in path)
    if complete:
        _check_against_legs(c, session, payload, legs, path, graph, claimed_scalar, claimed_dur, claimed_travel)
        return

    # legs missing / invalid: leg-level claims on routes that do not exist cannot be true
    for i, (leg, ge) in enumerate(zip(legs, path)):
        if ge is None:
            for k in ("regular_fare_min_bdt", "regular_fare_max_bdt", "student_fare_min_bdt", "student_fare_max_bdt", "fare_bdt"):
                if _num(leg.get(k)) is not None:
                    c.add("FARE_MISMATCH", "A fare was claimed for a route that cannot be priced.", leg[k], f"legs[{i}].{k}")
            if _num(leg.get("travel_time_min")) is not None:
                c.add("DURATION_MISMATCH", "A time was claimed for a route that cannot be timed.",
                      leg["travel_time_min"], f"legs[{i}].travel_time_min")
    # trip-level claims: compare with what the engine finds between the endpoints
    reference = None
    if origin and destination and origin != destination:
        found = rs.search_routes(session, origin, destination, session_id=session_id, scenario=scenario)
        reference = [o for o in found.get("routes", [])]
    ranges = []
    for o in reference or []:
        for pre in ("regular", "student"):
            lo, hi = o.get(f"total_{pre}_fare_min_bdt"), o.get(f"total_{pre}_fare_max_bdt")
            if lo is not None:
                ranges.append((lo, hi))
    durations = [o.get("total_duration_min") for o in reference or []]

    def fare_claim(lo, hi, field, value):
        if not any(_range_ok(lo, hi, rlo, rhi) for rlo, rhi in ranges):
            where = f"between {origin} and {destination}" if reference is not None else "(no real route to compare with)"
            c.add("FARE_MISMATCH", f"Claimed fare does not match calculated fare range {where}.", value, field)

    if claimed_scalar is not None:
        fare_claim(claimed_scalar, claimed_scalar, "claimed_fare_bdt", claimed_scalar)
    if has_total_claims:
        lo = _num(payload.get("total_regular_fare_min_bdt")) or _num(payload.get("total_student_fare_min_bdt"))
        hi = _num(payload.get("total_regular_fare_max_bdt")) or _num(payload.get("total_student_fare_max_bdt"))
        if lo is not None and hi is not None:
            fare_claim(lo, hi, "total_regular_fare_min_bdt", [lo, hi])
    for value, field in ((claimed_dur, "claimed_duration_min"), (claimed_travel, "total_travel_time_min")):
        if _num(value) is not None and not any(duration_ok(float(value), d) for d in durations if d is not None):
            c.add("DURATION_MISMATCH", "Claimed duration does not match the engine's calculated duration for any real "
                  "route between these hubs." if durations else "Claimed duration cannot be verified: no real route found.",
                  value, field)


def _trip_numbers(path: list[rs.GraphEdge], leg_fares: list, graph: rs.Graph) -> dict:
    priced = all(f is not None for f in leg_fares)
    trip = fs.calculate_trip_fare(list(leg_fares), prefer_student=True) if priced else None
    times = [ge.time_min for ge in path]
    travel = None if any(t is None for t in times) else sum(times)
    vehicle = [ge for ge in path if ge.mode != WALK]
    transfers = sum(1 for a, b in zip(vehicle, vehicle[1:]) if a.mode != b.mode)
    wait = transfers * graph.transfer_wait_min
    return {"trip": trip, "travel": travel, "duration": None if travel is None else travel + wait}


def _check_against_legs(c: _Check, session: Session, payload: dict, legs: list[dict], path: list[rs.GraphEdge],
                        graph: rs.Graph, claimed_scalar, claimed_dur, claimed_travel) -> None:
    leg_fares = rs._price_path(session, graph, path)
    for i, (leg, ge, lf) in enumerate(zip(legs, path, leg_fares)):
        f = f"legs[{i}]"
        fare_keys = ("regular_fare_min_bdt", "regular_fare_max_bdt", "student_fare_min_bdt", "student_fare_max_bdt", "fare_bdt")
        if lf is None:
            for k in fare_keys:
                if _num(leg.get(k)) is not None:
                    c.add("FARE_MISMATCH", f"The dataset has no fare for route {ge.route_id}; none can be stated.",
                          leg[k], f"{f}.{k}")
                    break
        else:
            exact_metro = lf.fare_source == "metro_matrix"
            if lf.mode == WALK and any(_num(leg.get(k)) not in (None, 0.0) for k in fare_keys):
                c.add("FARE_MISMATCH", "Walking is free (fare must be 0).", leg.get("regular_fare_min_bdt"), f"{f}.regular_fare_min_bdt")
            _check_fare_claim(c, _first(leg, "regular_fare_min_bdt", "fare_bdt"), _first(leg, "regular_fare_max_bdt", "fare_bdt"),
                              lf.regular_fare_min_bdt, lf.regular_fare_max_bdt, f"Leg {i + 1} ({lf.mode})", f"{f}.regular_fare_min_bdt",
                              [leg.get("regular_fare_min_bdt"), leg.get("regular_fare_max_bdt")], exact_metro)
            smin, smax = _num(leg.get("student_fare_min_bdt")), _num(leg.get("student_fare_max_bdt"))
            if smin is not None or smax is not None:
                smin, smax = (smin if smin is not None else smax), (smax if smax is not None else smin)
                if abs(smin - lf.student_fare_min_bdt) > FARE_TOLERANCE_BDT or abs(smax - lf.student_fare_max_bdt) > FARE_TOLERANCE_BDT:
                    rule = ("student discount applies (50% off, minimum 10 BDT for local buses)" if lf.student_discount_applies
                            else f"no student discount applies to {lf.mode}: the student fare equals the regular fare")
                    c.add("INVALID_STUDENT_RULE", f"Leg {i + 1}: {rule}; expected {lf.student_fare_min_bdt:.2f}-"
                          f"{lf.student_fare_max_bdt:.2f} BDT.", [smin, smax], f"{f}.student_fare_min_bdt")
        _check_duration_claim(c, leg.get("travel_time_min"), ge.time_min, f"Leg {i + 1}", f"{f}.travel_time_min")

    n = _trip_numbers(path, leg_fares, graph)
    trip = n["trip"]
    if trip is None:
        for k in ("total_regular_fare_min_bdt", "total_student_fare_min_bdt"):
            if _num(payload.get(k)) is not None:
                c.add("FARE_MISMATCH", "Some leg has no dataset fare, so no total can be stated.", payload[k], k)
        if claimed_scalar is not None:
            c.add("FARE_MISMATCH", "Some leg has no dataset fare, so no total can be stated.", claimed_scalar, "claimed_fare_bdt")
    else:
        all_metro = all(l.fare_source == "metro_matrix" or l.mode == WALK for l in leg_fares)
        _check_fare_claim(c, payload.get("total_regular_fare_min_bdt"), payload.get("total_regular_fare_max_bdt"),
                          trip.total_regular_fare_min_bdt, trip.total_regular_fare_max_bdt, "Trip total", "total_regular_fare_min_bdt",
                          [payload.get("total_regular_fare_min_bdt"), payload.get("total_regular_fare_max_bdt")], all_metro)
        _check_fare_claim(c, payload.get("total_student_fare_min_bdt"), payload.get("total_student_fare_max_bdt"),
                          trip.total_student_fare_min_bdt, trip.total_student_fare_max_bdt, "Trip student total",
                          "total_student_fare_min_bdt", [payload.get("total_student_fare_min_bdt"),
                                                         payload.get("total_student_fare_max_bdt")], all_metro, student=True)
        if claimed_scalar is not None:
            ok = any(_range_ok(claimed_scalar, claimed_scalar, lo, hi) for lo, hi in (
                (trip.total_regular_fare_min_bdt, trip.total_regular_fare_max_bdt),
                (trip.total_student_fare_min_bdt, trip.total_student_fare_max_bdt)))
            if not ok:
                c.add("FARE_MISMATCH", f"Claimed fare does not match calculated fare range "
                      f"{trip.total_regular_fare_min_bdt:.2f}-{trip.total_regular_fare_max_bdt:.2f} BDT.",
                      claimed_scalar, "claimed_fare_bdt")
    _check_duration_claim(c, claimed_dur, n["duration"], "Trip duration", "total_duration_min")
    _check_duration_claim(c, claimed_travel, n["travel"], "Trip travel time", "total_travel_time_min")


# ============================================================================= public API
def validate_ai_route_option(session: Session, route_option: dict, scenario: str | None = None,
                             session_id: str | None = None, graph: rs.Graph | None = None) -> GroundingResult:
    """Validate one route option (the shape returned by trip search) produced or edited by an AI."""
    return _validate(session, route_option or {}, scenario=scenario, session_id=session_id, graph=graph).result()


def validate_route_payload(session: Session, payload: dict, scenario: str | None = None,
                           session_id: str | None = None) -> GroundingResult:
    """Validate a route payload: full legs, or just `route_ids` + `hub_ids` + `claimed_*` claims."""
    return _validate(session, payload or {}, scenario=scenario, session_id=session_id).result()


_MONEY_RE = re.compile(r"(?:(?:tk\.?|bdt|taka|৳)\s*(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s*(?:tk\b|bdt\b|taka\b|৳))", re.I)
_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:min\b|mins\b|minutes?\b)", re.I)


def _check_rule_cards(c: _Check, session: Session, payload: dict) -> list[float]:
    """Verify fare-rule / mode-rate cards against the database; returns the verified numbers (usable in text)."""
    from app.models import FareRule, ModeRate

    allowed: list[float] = []
    specs = (("fare_rules", FareRule, ("student_discount_percent", "minimum_student_fare_bdt")),
             ("mode_rates", ModeRate, ("base_fare_bdt", "per_km_bdt", "min_fare_bdt", "average_speed_kmh")))
    for key, model, fields in specs:
        for i, card in enumerate(payload.get(key) or []):
            row = session.get(model, card.get("mode")) if isinstance(card, dict) else None
            if row is None:
                c.add("UNKNOWN_MODE", f"No {key[:-1].replace('_', ' ')} for mode '{card.get('mode') if isinstance(card, dict) else card}'.",
                      card.get("mode") if isinstance(card, dict) else card, f"{key}[{i}]")
                continue
            for f in fields:
                db_value = getattr(row, f)
                if f in card and _num(card[f]) != db_value and not (card[f] is None and db_value is None):
                    c.add("FARE_MISMATCH", f"{key[:-1]} {card['mode']}.{f} is {db_value}, not {card[f]}.", card[f], f"{key}[{i}].{f}")
                elif _num(db_value) is not None:
                    allowed.append(float(db_value))
    return allowed


def validate_chat_payload(session: Session, chat_response: dict, scenario: str | None = None,
                          session_id: str | None = None, user_figures: list[float] | None = None) -> GroundingResult:
    """Validate an assistant reply: every route option / alert in `payload`, plus the fares and minutes quoted
    in `reply_text` (they must match a grounded option; a quoted fare with no route behind it is rejected)."""
    c = _Check()
    chat_response = chat_response or {}
    payload = chat_response.get("payload") or {}
    options: list[dict] = []
    for key in ("route_options", "routes"):
        options += [o for o in payload.get(key) or [] if isinstance(o, dict)]
    for key in ("route_option", "selected_route"):
        if isinstance(payload.get(key), dict):
            options.append(payload[key])
    if isinstance(chat_response.get("route_option"), dict):
        options.append(chat_response["route_option"])
    scen = scenario or payload.get("scenario")
    graph = rs.build_graph(session, session_id, scenario=fs.normalize_scenario(scen)) if options else None
    results = []
    for i, option in enumerate(options):
        sub = _validate(session, option, scenario=scen, session_id=session_id, graph=graph)
        for v in sub.violations:
            c.violations.append(v.model_copy(update={"field": f"payload.routes[{i}]" + (f".{v.field}" if v.field else "")}))
        for rid in sub.route_ids:
            c.see_route(rid)
        for hid in sub.hub_ids:
            c.see_hub(hid)
        results.append(option)
    extra = {k: payload[k] for k in ("alerts", "alert_ids", "routing_alert_ids") if k in payload}
    if extra:
        _check_alerts(c, session, extra, [])
    entities = chat_response.get("entities") or {}
    for key in ("origin", "destination", "origin_hub", "destination_hub"):
        _check_hub_field(c, session, entities.get(key), f"entities.{key}")
    for i, h in enumerate(entities.get("hub_ids") or []):
        _check_hub_field(c, session, h, f"entities.hub_ids[{i}]")

    allowed_numbers = _check_rule_cards(c, session, payload) + [float(x) for x in (user_figures or [])]
    text = str(chat_response.get("reply_text") or "")
    money = [float(a or b) for a, b in _MONEY_RE.findall(text)]
    minutes = [float(m) for m in _MIN_RE.findall(text)]
    if money or minutes:
        fare_ranges: list[tuple[float, float]] = []
        times: list[float | None] = []
        for o in results:
            for pre in ("regular", "student"):
                lo, hi = _num(o.get(f"total_{pre}_fare_min_bdt")), _num(o.get(f"total_{pre}_fare_max_bdt"))
                if lo is not None and hi is not None:
                    fare_ranges.append((lo, hi))
            for l in o.get("legs") or []:
                for pre in ("regular", "student"):
                    lo, hi = _num(l.get(f"{pre}_fare_min_bdt")), _num(l.get(f"{pre}_fare_max_bdt"))
                    if lo is not None and hi is not None:
                        fare_ranges.append((lo, hi))
                times.append(_num(l.get("travel_time_min")))
            times += [_num(o.get("total_duration_min")), _num(o.get("total_travel_time_min"))]
        if not c.violations:  # numbers are only trusted next to a payload that itself passed
            for m in money:
                if not any(lo - FARE_TOLERANCE_BDT <= m <= hi + FARE_TOLERANCE_BDT for lo, hi in fare_ranges) and not any(
                        abs(m - a) <= FARE_TOLERANCE_BDT for a in allowed_numbers):
                    c.add("FARE_MISMATCH", "A fare in the reply text is not backed by any grounded route option.", m, "reply_text")
            for m in minutes:
                if not any(t is not None and duration_ok(m, t) for t in times):
                    c.add("DURATION_MISMATCH", "A duration in the reply text is not backed by any grounded route option.", m, "reply_text")
    return c.result()


def validate_search_result(session: Session, result: dict, scenario: str | None, session_id: str | None) -> dict:
    """Ground every option of a deterministic trip-search result (normally all pass). Options that fail are
    dropped from `result["routes"]` and reported, so nothing ungrounded is ever displayed."""
    routes = result.get("routes") or []
    if not routes:
        return GroundingResult().model_dump() | {"badge": "grounded", "options_checked": 0, "options_dropped": 0}
    graph = rs.build_graph(session, session_id, scenario=scenario or "normal")
    merged = _Check()
    keep = []
    for i, option in enumerate(routes):
        sub = _validate(session, option, scenario=scenario, session_id=session_id, graph=graph)
        if sub.violations:
            for v in sub.violations:
                merged.violations.append(v.model_copy(update={"field": f"routes[{i}]" + (f".{v.field}" if v.field else "")}))
        else:
            keep.append(option)
        for rid in sub.route_ids:
            merged.see_route(rid)
        for hid in sub.hub_ids:
            merged.see_hub(hid)
    dropped = len(routes) - len(keep)
    result["routes"] = keep
    out = merged.result(fallback_used=bool(dropped))
    return out.model_dump() | {"badge": out.badge(), "options_checked": len(routes), "options_dropped": dropped}


def fake_grounding_test(session: Session, payload: dict, scenario: str | None = None,
                        session_id: str | None = None) -> dict:
    """Judge demo: run an AI-style payload through grounding and show it blocked, with the engine's real answer."""
    result = validate_route_payload(session, payload, scenario, session_id)
    out = {
        "grounded": result.grounded, "blocked": not result.grounded, "safe_for_display": result.safe_for_display,
        "violations": [v.model_dump(exclude_none=False) for v in result.violations],
        "checked_route_ids": result.checked_route_ids, "checked_hub_ids": result.checked_hub_ids,
        "fare_check_passed": result.fare_check_passed, "duration_check_passed": result.duration_check_passed,
        "fallback_used": not result.grounded,
        "fallback_action": FAKE_FALLBACK if not result.grounded else None,
    }
    ids = payload.get("hub_ids") or []
    if not result.grounded and len(ids) >= 2:
        engine = rs.search_routes(session, ids[0], ids[-1], session_id=session_id, scenario=scenario or "normal")
        if engine.get("routes"):
            best = engine["routes"][0]
            out["engine_result"] = {"route_option_id": best["route_option_id"], "legs": [l["route_id"] for l in best["legs"]],
                                    "total_regular_fare_min_bdt": best["total_regular_fare_min_bdt"],
                                    "total_regular_fare_max_bdt": best["total_regular_fare_max_bdt"],
                                    "total_duration_min": best["total_duration_min"]}
    return out


# ============================================================================= reliability
def build_reliability_payload(session: Session, report_id: str) -> dict:
    """Why an alert is (or is not) trusted: votes, decay, admin approval, expiry."""
    from app.services import alert_service

    report = session.get(Report, report_id)
    if report is None:
        raise api_error("REPORT_NOT_FOUND", f"No report '{report_id}'.", details={"report_id": report_id})
    alert_service.refresh_decay(session, report)
    session.commit()
    session.refresh(report)
    now = utcnow()
    live = report.status in alert_rules.LIVE_STATUSES
    reasons: list[str] = []
    if report.status == "verified" and report.admin_approved:
        reasons.append("Admin verified")
    elif report.status == "pending":
        reasons.append("Awaiting admin verification")
    elif report.status in ("rejected", "resolved", "expired"):
        reasons.append(f"Report is {report.status}")
    if report.upvotes >= 2:
        reasons.append("Multiple recent confirmations" if report.freshness >= 0.5 else "Multiple confirmations (aging)")
    elif report.upvotes == 1:
        reasons.append("One confirmation")
    if report.downvotes > report.upvotes:
        reasons.append("Disputed by the community")
    reasons.append("Contradictory report flagged" if report.contradiction_flag else "No contradictory report")
    if report.needs_admin_review:
        reasons.append("Flagged for admin review")
    reasons.append("Within expiry window" if live and now < report.expires_at else "Outside expiry window")
    if live and report.freshness < 0.5:
        reasons.append("Confidence has decayed over time")
    if report.status == "verified" and not report.affects_routing:
        reasons.append("Not affecting routing (confidence below threshold or no hub/routes)")
    return {
        "report_id": report.report_id, "status": report.status, "confidence": round(report.confidence, 4),
        "freshness": round(report.freshness, 4), "upvotes": report.upvotes, "downvotes": report.downvotes,
        "community_score": report.community_score,
        "last_verified_at": report.last_verified_at.isoformat() if report.last_verified_at else None,
        "expires_at": report.expires_at.isoformat(), "admin_approved": report.admin_approved,
        "affects_routing": report.affects_routing, "needs_admin_review": report.needs_admin_review,
        "contradiction_flag": report.contradiction_flag, "reasons": reasons,
    }
