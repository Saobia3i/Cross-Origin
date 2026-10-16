"""Per-session disruption simulation ("what if it rains / rush hour / this intersection is blocked").

A simulation is a row in `simulations` for one session_id. It is VIRTUAL: effects are computed from the
dataset and applied while the routing graph is built (routing_service.build_graph), so nothing is ever
written to route_edges. Other sessions, and the global network, are unaffected.

Scenario -> effects (only dataset-backed; nothing is invented):
  heavy_rain            fare 'rain'            time = Time_Rain_min            closes flood_prone edges (in zone_hub if given)
  peak_rush_hour        fare 'peak'            time = Time_Peak_Hour_min       no closures
  blocked_intersection  fare 'detour'          time = detour time              closes active non-walk edges touching zone_hub
  regular_holiday       fare 'regular_holiday' no time effect                  no closures
  festive_holiday       fare 'festive_holiday' no time effect                  no closures
Where a scenario time is missing the normal time is used and a TIME_*_DATA_MISSING warning says so.
Extra transfer wait comes only from dataset_config/routing_config.json -> scenario_transfer_wait_min (empty).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.enums import EventType, ScenarioType, SimulationStatus
from app.models import Hub, RouteEdge, Simulation, utcnow
from app.responses import api_error
from app.schemas import ScenarioEffects
from app.services import dataset_manifest as dm
from app.services import event_service, search_cache

CONFIG = dm.load_config("routing_config.json")
WALK = "walk"
SCENARIO_FARE = {
    ScenarioType.heavy_rain.value: "rain",
    ScenarioType.peak_rush_hour.value: "peak",
    ScenarioType.blocked_intersection.value: "detour",
    ScenarioType.detour.value: "detour",
    ScenarioType.regular_holiday.value: "regular_holiday",
    ScenarioType.festive_holiday.value: "festive_holiday",
}
SCENARIO_TIME = {"rain": ("time_rain_min", "TIME_RAIN_DATA_MISSING"),
                 "peak": ("time_peak_min", "TIME_PEAK_DATA_MISSING"),
                 "detour": ("time_detour_min", "TIME_DETOUR_DATA_MISSING")}


class SimulationResult(BaseModel):
    scenario_id: Optional[str] = None
    session_id: str
    scenario_type: Optional[str] = None
    status: str
    effects: dict = Field(default_factory=dict)
    broken_segments: list[dict] = Field(default_factory=list)
    recalculated_routes: list[dict] = Field(default_factory=list)
    before_after_comparison: Optional[dict] = None
    zone_hub: Optional[str] = None
    expires_at: Optional[datetime] = None


class ComparisonResult(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    prefer_student_fare: bool = False
    available: bool = False
    rerouted: bool = False
    original_route_id: Optional[str] = None
    original_route_ids: list[str] = []
    original_duration_min: Optional[float] = None
    original_fare_min: Optional[float] = None
    original_fare_max: Optional[float] = None
    new_route_id: Optional[str] = None
    new_route_ids: list[str] = []
    new_duration_min: Optional[float] = None
    new_fare_min: Optional[float] = None
    new_fare_max: Optional[float] = None
    added_time_min: Optional[float] = None
    added_fare_min: Optional[float] = None
    added_fare_max: Optional[float] = None
    note: Optional[str] = None
    before: dict = Field(default_factory=dict)  # full best option before / after (fare ranges, legs)
    after: dict = Field(default_factory=dict)


class SessionGraphContext(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    session_id: str
    scenario: Optional[Any] = None
    effects: Optional[ScenarioEffects] = None
    graph: Optional[Any] = None


# ============================================================================= effects
def get_active_scenario(session: Session, session_id: str | None, now: datetime | None = None) -> Simulation | None:
    if not session_id:
        return None
    now = now or utcnow()
    for sim in session.exec(select(Simulation).where(
            Simulation.session_id == session_id, Simulation.status == SimulationStatus.active.value,
    ).order_by(Simulation.created_at.desc(), Simulation.scenario_id)).all():
        if sim.expires_at is None or sim.expires_at > now:
            return sim
    return None


def compute_effects(session: Session, scenario_type: str, zone_hub: str | None,
                    edges: list[RouteEdge] | None = None) -> ScenarioEffects:
    fare = SCENARIO_FARE.get(scenario_type, "normal")
    edges = edges if edges is not None else list(session.exec(select(RouteEdge)).all())
    eff = ScenarioEffects(
        scenario_type=scenario_type, fare_scenario=fare, zone_hub=zone_hub,
        extra_transfer_wait_min=float(CONFIG.get("scenario_transfer_wait_min", {}).get(scenario_type, 0) or 0))

    def touches(e: RouteEdge) -> bool:
        return zone_hub in (e.origin_hub, e.destination_hub)

    def close(e: RouteEdge, why: str) -> None:
        if e.status != "closed" and e.route_id not in eff.closure_reasons:
            eff.closure_reasons[e.route_id] = why

    if scenario_type == ScenarioType.heavy_rain.value:
        flood = [e for e in edges if e.flood_prone]
        for e in flood:
            if zone_hub is None or touches(e):
                close(e, "flood-prone route closed by heavy-rain simulation")
        if not flood:
            eff.warnings.append("NO_FLOOD_PRONE_DATA: the dataset flags no flood-prone routes, so the simulation "
                                "closes nothing; only rain fares and times change.")
    elif scenario_type in (ScenarioType.blocked_intersection.value, ScenarioType.detour.value) and zone_hub:
        for e in edges:
            if e.mode != WALK and touches(e):
                close(e, f"intersection {zone_hub} blocked (simulation)")
    elif scenario_type == ScenarioType.blocked_intersection.value:
        eff.warnings.append("ZONE_HUB_MISSING: blocked_intersection needs a zone_hub; nothing was closed.")

    column = SCENARIO_TIME.get(fare)
    if column:
        attr, code = column
        eff.time_scenario = fare
        for e in edges:
            value = getattr(e, attr)
            if value is not None and e.route_id not in eff.closure_reasons and e.status != "closed":
                eff.time_overrides[e.route_id] = float(value)
        missing = sum(1 for e in edges if getattr(e, attr) is None and e.mode != WALK)
        if missing:
            eff.warnings.append(f"{code}: {missing} of {len(edges)} routes have no {fare} travel time in the dataset; "
                                "their normal time is used (no time multiplier is invented).")
    eff.closed_route_ids = sorted(eff.closure_reasons)
    return eff


def get_scenario_effects(session: Session, session_id: str | None, now: datetime | None = None) -> ScenarioEffects | None:
    """Effects of the session's active simulation, computed from current data; None when there is none."""
    sim = get_active_scenario(session, session_id, now)
    return compute_effects(session, sim.scenario_type, sim.zone_hub) if sim else None


def graph_effects(eff: ScenarioEffects) -> dict:
    """The dict form routing_service.build_graph understands."""
    out: dict[str, Any] = {"fare_scenario": eff.fare_scenario, "closed_route_ids": eff.closure_reasons}
    if eff.time_scenario:
        out["time_scenario"] = eff.time_scenario
    if eff.extra_transfer_wait_min:
        out["transfer_wait_min"] = eff.extra_transfer_wait_min
    return out


def build_session_graph_context(session: Session, session_id: str, scenario: str = "normal") -> SessionGraphContext:
    """The session's active scenario, its effects, and the routing graph built with them."""
    from app.services import routing_service as rs

    sim = get_active_scenario(session, session_id)
    return SessionGraphContext(session_id=session_id, scenario=sim,
                               effects=get_scenario_effects(session, session_id),
                               graph=rs.build_graph(session, session_id, scenario=scenario))


# ============================================================================= comparison
def _best(result: dict) -> dict | None:
    return result["routes"][0] if result.get("routes") else None


def compare_before_after(session: Session, session_id: str, origin: str | None, destination: str | None,
                         prefer_student_fare: bool = False) -> ComparisonResult:
    """Best route without vs with the session's simulation (verified alerts count in both)."""
    from app.services import routing_service as rs

    cmp = ComparisonResult(origin=origin, destination=destination, prefer_student_fare=prefer_student_fare)
    if not origin or not destination:
        cmp.note = "NO_JOURNEY: give an origin and destination (or run a trip search in this session first)."
        return cmp
    before = rs.search_routes(session, origin, destination, prefer_student_fare=prefer_student_fare, session_id=None)
    after = rs.search_routes(session, origin, destination, prefer_student_fare=prefer_student_fare, session_id=session_id)
    for res in (before, after):
        if res.get("error"):
            cmp.note = f"{res['error']['code']}: {res['error']['message']}"
            return cmp
    b, a = _best(before), _best(after)
    cmp.available = True
    fare_keys = ("total_student_fare_min_bdt", "total_student_fare_max_bdt") if prefer_student_fare else \
        ("total_regular_fare_min_bdt", "total_regular_fare_max_bdt")
    if b:
        cmp.original_route_ids = [l["route_id"] for l in b["legs"]]
        cmp.original_route_id = "+".join(cmp.original_route_ids)
        cmp.original_duration_min = b["total_duration_min"]
        cmp.original_fare_min, cmp.original_fare_max = b[fare_keys[0]], b[fare_keys[1]]
        cmp.before = b
    if a:
        cmp.new_route_ids = [l["route_id"] for l in a["legs"]]
        cmp.new_route_id = "+".join(cmp.new_route_ids)
        cmp.new_duration_min = a["total_duration_min"]
        cmp.new_fare_min, cmp.new_fare_max = a[fare_keys[0]], a[fare_keys[1]]
        cmp.after = a
    cmp.rerouted = cmp.original_route_ids != cmp.new_route_ids

    def diff(new, old):
        return None if new is None or old is None else new - old

    cmp.added_time_min = diff(cmp.new_duration_min, cmp.original_duration_min)
    cmp.added_fare_min = diff(cmp.new_fare_min, cmp.original_fare_min)
    cmp.added_fare_max = diff(cmp.new_fare_max, cmp.original_fare_max)
    if not b:
        cmp.note = "NO_ROUTE_BEFORE: no route exists between these hubs even without the simulation."
    elif not a:
        cmp.note = "NO_ROUTE_AFTER: the simulation leaves no route between these hubs."
    return cmp


# ============================================================================= apply / reset
def _journey(session_id: str, origin, destination, prefer) -> tuple[str | None, str | None, bool]:
    if origin and destination:
        return origin, destination, bool(prefer)
    last = search_cache.recall_query(session_id)
    if last:
        return last["origin"], last["destination"], bool(prefer if prefer is not None else last.get("prefer_student_fare"))
    return None, None, bool(prefer)


def apply_simulation(session: Session, session_id: str, scenario_type: str, zone_hub: str | None = None,
                     user_id: int | None = None, origin: str | None = None, destination: str | None = None,
                     prefer_student_fare: bool | None = None) -> SimulationResult:
    """Start (replacing any active one) this session's simulation. Never touches route_edges."""
    from app.services import routing_service as rs

    scenario_type = scenario_type.value if isinstance(scenario_type, ScenarioType) else scenario_type
    if scenario_type not in SCENARIO_FARE:
        raise api_error("VALIDATION_ERROR", f"Unsupported scenario_type '{scenario_type}'.", "scenario_type")
    zone_id = None
    if zone_hub:
        resolved = rs.resolve_hub(session, zone_hub)
        if resolved.hub is None:
            raise api_error("UNKNOWN_HUB" if resolved.code != "AMBIGUOUS_HUB" else "AMBIGUOUS_HUB", resolved.message,
                            "zone_hub", {"candidates": resolved.candidates, "suggestions": resolved.suggestions})
        zone_id = resolved.hub.hub_id
    if scenario_type == ScenarioType.blocked_intersection.value and not zone_id:
        raise api_error("ZONE_HUB_REQUIRED", "blocked_intersection needs a zone_hub (the blocked intersection).", "zone_hub")

    now = utcnow()
    replaced = []
    for old in session.exec(select(Simulation).where(Simulation.session_id == session_id,
                                                     Simulation.status == SimulationStatus.active.value)).all():
        old.status, old.reset_at = SimulationStatus.reset.value, now
        session.add(old)
        replaced.append(old.scenario_id)
    effects = compute_effects(session, scenario_type, zone_id)
    sim = Simulation(
        scenario_id=f"sim_{uuid.uuid4().hex[:12]}", session_id=session_id, scenario_type=scenario_type,
        zone_hub=zone_id, status=SimulationStatus.active.value, effects_json={},  # effects are derived live
        created_at=now, expires_at=now + timedelta(minutes=int(CONFIG.get("simulation_ttl_min", 120))))
    session.add(sim)
    session.flush()

    o, d, prefer = _journey(session_id, origin, destination, prefer_student_fare)
    comparison = compare_before_after(session, session_id, o, d, prefer)
    broken = []
    if comparison.available:
        broken = [{"route_id": rid, "reason": effects.closure_reasons[rid]}
                  for rid in comparison.original_route_ids if rid in effects.closure_reasons]
    summary = {"scenario_id": sim.scenario_id, "scenario_type": scenario_type, "zone_hub": zone_id,
               "fare_scenario": effects.fare_scenario, "closed_count": len(effects.closed_route_ids),
               "warnings": [w.split(":", 1)[0] for w in effects.warnings], "replaced": replaced, "user_id": user_id}
    event_service.publish_event(session, EventType.simulation_applied, summary, session_id=session_id)
    event_service.publish_event(session, EventType.route_recalculated,
                                {"reason": "simulation_applied", "scenario_id": sim.scenario_id,
                                 "closed_count": len(effects.closed_route_ids), "rerouted": comparison.rerouted},
                                session_id=session_id)
    session.commit()
    session.refresh(sim)
    return SimulationResult(
        scenario_id=sim.scenario_id, session_id=session_id, scenario_type=scenario_type, status=sim.status,
        effects=effects.model_dump(mode="json"), broken_segments=broken,
        recalculated_routes=_routes_after(session, session_id, o, d, prefer),
        before_after_comparison=comparison.model_dump(mode="json"), zone_hub=zone_id, expires_at=sim.expires_at)


def _routes_after(session: Session, session_id: str, origin, destination, prefer) -> list[dict]:
    from app.services import routing_service as rs

    if not origin or not destination:
        return []
    res = rs.search_routes(session, origin, destination, prefer_student_fare=bool(prefer), session_id=session_id)
    return res.get("routes", [])


def reset_simulation(session: Session, session_id: str, user_id: int | None = None) -> SimulationResult:
    """End this session's active simulation; routing for the session returns to the global network."""
    now = utcnow()
    active = session.exec(select(Simulation).where(Simulation.session_id == session_id,
                                                   Simulation.status == SimulationStatus.active.value)).all()
    if not active:
        return SimulationResult(session_id=session_id, status="none")
    last = max(active, key=lambda s: s.created_at)
    effects = compute_effects(session, last.scenario_type, last.zone_hub)
    for sim in active:
        sim.status, sim.reset_at = SimulationStatus.reset.value, now
        session.add(sim)
    event_service.publish_event(session, EventType.simulation_reset,
                                {"scenario_id": last.scenario_id, "scenario_type": last.scenario_type, "user_id": user_id},
                                session_id=session_id)
    event_service.publish_event(session, EventType.route_recalculated,
                                {"reason": "simulation_reset", "scenario_id": last.scenario_id}, session_id=session_id)
    session.commit()
    return SimulationResult(scenario_id=last.scenario_id, session_id=session_id, scenario_type=last.scenario_type,
                            status=SimulationStatus.reset.value, effects=effects.model_dump(mode="json"),
                            zone_hub=last.zone_hub)
