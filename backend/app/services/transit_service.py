"""Read models for the transit browsing endpoints: hubs with DERIVED status, routes with disruption info.

Status is always computed from current data (never stored) and is session-aware, because it is built
from the same overlay the router uses (routing_service.build_graph): admin closures, verified unexpired
alerts, and the caller's own simulation.

Hub status rules (documented in docs/TRANSIT_API.md):
  blocked  every route touching the hub is closed (admin, verified alert, or this session's simulation)
  delayed  not blocked, and any of: some (not all) routes closed; a verified alert adds delay at the hub
           or on a route touching it; this session's simulation targets the hub or slows a route there
  normal   otherwise
There is no admin hub-override table yet, so none is applied.
"""
from __future__ import annotations

from collections import defaultdict

from sqlmodel import Session, select

from app.models import FareRule, Hub, ModeRate, RouteEdge, ScenarioMultiplier
from app.schemas import (
    FareRuleOut, HubOut, ModeRateOut, NetworkOut, RouteEdgeOut, ScenarioMultiplierOut,
)
from app.services import routing_service as rs


def _cause(reason: str) -> str:
    if reason.startswith("admin"):
        return "admin"
    if reason.startswith("simulation"):
        return "simulation"
    return "verified_alert"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def hub_statuses(session: Session, session_id: str | None = None) -> list[HubOut]:
    graph = rs.build_graph(session, session_id)
    alerts = rs.load_active_alerts(session)
    hubs = session.exec(select(Hub).order_by(Hub.hub_id)).all()
    routes_at: dict[str, set[str]] = defaultdict(set)
    for e in session.exec(select(RouteEdge)).all():
        routes_at[e.origin_hub].add(e.route_id)
        routes_at[e.destination_hub].add(e.route_id)
    sim_slowed = {ge.route_id for ge in graph.edges if any(w.startswith("SIMULATION_DELAY") for w in ge.warnings)}

    out: list[HubOut] = []
    for hub in hubs:
        mine = routes_at.get(hub.hub_id, set())
        alert_ids = [a.report_id for a in alerts
                     if a.affected_hub == hub.hub_id or mine & set(a.affected_route_ids_json or [])]
        closed = {r: graph.closed[r] for r in mine if r in graph.closed}
        causes = defaultdict(int)
        for reason in closed.values():
            causes[_cause(reason)] += 1
        by = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(causes.items()))

        status, reason = "normal", None
        if mine and len(closed) == len(mine):
            status, reason = "blocked", f"all {_plural(len(mine), 'route')} at this hub are closed ({by})"
        else:
            bits = []
            if closed:
                bits.append(f"{len(closed)} of {_plural(len(mine), 'route')} closed ({by})")
            delay_alerts = [a.report_id for a in alerts if a.extra_time_min and a.extra_time_min > 0 and (
                a.affected_hub == hub.hub_id or mine & set(a.affected_route_ids_json or []))]
            if delay_alerts:
                bits.append(f"verified alert(s) add delay: {', '.join(delay_alerts)}")
            if graph.sim_scenario_id and (graph.zone_hub == hub.hub_id or mine & sim_slowed):
                bits.append(f"your simulation {graph.sim_scenario_id} affects this hub")
            if bits:
                status, reason = "delayed", "; ".join(bits)
        out.append(HubOut(
            hub_id=hub.hub_id, name=hub.name, name_bn=hub.name_bn, aliases=list(hub.aliases_json or []),
            latitude=hub.latitude, longitude=hub.longitude, type=hub.type, status=status,
            status_reason=reason, active_alert_count=len(alert_ids)))
    return out


def hub_status_summaries(session: Session, session_id: str | None = None) -> list[dict]:
    alerts = rs.load_active_alerts(session)
    routes_at: dict[str, set[str]] = defaultdict(set)
    for e in session.exec(select(RouteEdge)).all():
        routes_at[e.origin_hub].add(e.route_id)
        routes_at[e.destination_hub].add(e.route_id)
    out = []
    for h in hub_statuses(session, session_id):
        mine = routes_at.get(h.hub_id, set())
        ids = [a.report_id for a in alerts
               if a.affected_hub == h.hub_id or mine & set(a.affected_route_ids_json or [])]
        out.append({"hub_id": h.hub_id, "status": h.status.value, "reason": h.status_reason,
                    "active_alert_ids": ids})
    return out


def route_views(session: Session, session_id: str | None = None, mode: str | None = None,
                hub_id: str | None = None, status: str | None = None,
                include_geometry: bool = False) -> list[RouteEdgeOut]:
    graph = rs.build_graph(session, session_id)
    alerts = rs.load_active_alerts(session)
    alerts_by_route: dict[str, list[str]] = defaultdict(list)
    for a in alerts:
        for rid in a.affected_route_ids_json or []:
            alerts_by_route[rid].append(a.report_id)
    first_edge = {}
    for ge in graph.edges:
        first_edge.setdefault(ge.route_id, ge)

    out: list[RouteEdgeOut] = []
    for e in session.exec(select(RouteEdge).order_by(RouteEdge.route_id)).all():
        if mode and e.mode != mode:
            continue
        if hub_id and hub_id not in (e.origin_hub, e.destination_hub):
            continue
        reason = graph.closed.get(e.route_id)
        effective = "closed" if reason else "active"
        if status and effective != status:
            continue
        ge = first_edge.get(e.route_id)
        extra = graph.delayed.get(e.route_id, 0.0) + (ge.sim_added_time_min if ge else 0.0)
        out.append(RouteEdgeOut(
            route_id=e.route_id, mode=e.mode, origin_hub=e.origin_hub, destination_hub=e.destination_hub,
            direction=e.direction, status=effective, closed_reason=reason,
            closed_by=_cause(reason) if reason else None, road_distance_km=e.road_distance_km,
            time_normal_min=e.time_normal_min, extra_time_min=extra,
            boarding_notes=list(e.boarding_notes_json or []), alighting_notes=list(e.alighting_notes_json or []),
            student_eligible=e.student_eligible, provider=e.provider, category=e.category,
            flood_prone=e.flood_prone, construction_prone=e.construction_prone,
            active_alert_ids=alerts_by_route.get(e.route_id, []),
            geometry=e.geometry_json if include_geometry else None))
    return out


def network(session: Session, session_id: str | None = None, include_geometry: bool = False) -> NetworkOut:
    return NetworkOut(
        hubs=hub_statuses(session, session_id),
        routes=route_views(session, session_id, include_geometry=include_geometry),
        fare_rules=[FareRuleOut.model_validate(r) for r in session.exec(select(FareRule).order_by(FareRule.mode))],
        mode_rates=[ModeRateOut.model_validate(r) for r in session.exec(select(ModeRate).order_by(ModeRate.mode))],
        scenario_multipliers=[ScenarioMultiplierOut.model_validate(r) for r in session.exec(
            select(ScenarioMultiplier).order_by(ScenarioMultiplier.mode, ScenarioMultiplier.scenario))],
    )
