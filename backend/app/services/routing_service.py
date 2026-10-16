"""Multi-modal routing over hubs (nodes) and route_edges (edges). See docs/ROUTING_ENGINE.md.

Nothing is invented: edges, hubs, coordinates and times come from the database; a missing time is
derived only from dataset mode speeds and distance, otherwise left null. Transfer wait is 0 unless a
session simulation supplies one. Fares come from fare_service (ranges). Every data-reading function
takes the SQLModel `session` as its first argument.

Disruptions are read-only overlays built per search:
  * admin-closed edges (route_edges.status == 'closed')           -> global, persistent
  * VERIFIED, unexpired reports with edge_closed / extra_time_min  -> global
  * the caller's active simulation (effects_json)                 -> that session only
Nothing here writes to route_edges.
"""
from __future__ import annotations

import heapq
import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Optional

from sqlmodel import Session, select

from app.enums import ReportStatus, SimulationStatus, TransitMode, values
from app.models import Hub, ModeRate, Report, RouteEdge, Simulation, utcnow
from app.schemas import (
    LegFareResult, NoRouteReason, NoRouteSuggestion, RouteGrounding, RouteLeg, RouteOption,
)
from app.services import dataset_manifest as dm
from app.services import alert_rules
from app.services import fare_service as fs
from app.services.fare_service import FareError
from app.services.hub_alias import compact_key, make_hub_id

OBJECTIVES = ("cheapest", "fastest", "fewest_transfers", "balanced")
WALK = "walk"
METRO = "mrt"
EARTH_RADIUS_KM = 6371.0088
CONFIG = dm.load_config("routing_config.json")
WEIGHTS = CONFIG.get("balanced_weights", {"fare": 0.4, "time": 0.4, "transfer": 0.2, "risk_per_point": 0.1})
TRANSFER_PENALTY = CONFIG.get("fewest_transfers_penalty", 1000)
MIN_ROUTES = CONFIG.get("min_routes", 3)
MAX_ROUTES = CONFIG.get("max_routes", 5)
MAX_ALTERNATE_ATTEMPTS = CONFIG.get("max_alternate_attempts", 12)
UNKNOWN_TIME_TIEBREAK_MIN = 1.0  # ranking tiebreak for fewest_transfers when an edge has no time
LEG_TIEBREAK = 0.01
SYMMETRIC_SOURCES = set(CONFIG.get("symmetric_sources", []))
ASSUME_REVERSE_UNKNOWN = bool(CONFIG.get("assume_reverse_for_unknown_direction", False))


# ============================================================================= geometry
def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _has_coords(hub: Hub | None) -> bool:
    return hub is not None and hub.latitude is not None and hub.longitude is not None


def _hub_distance_km(a: Hub, b: Hub) -> float:
    return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)


# ============================================================================= graph
@dataclass
class GraphEdge:
    """One traversable direction of a route_edge (an immutable view; the DB row is never changed)."""
    route_id: str
    mode: str
    from_hub: str
    to_hub: str
    edge: RouteEdge
    base_time_min: Optional[float]          # scenario time (multiplier applied), None if unknown
    alert_extra_time_min: float = 0.0       # from verified reports
    sim_added_time_min: float = 0.0         # from the session simulation
    risk_points: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def time_min(self) -> Optional[float]:
        if self.base_time_min is None:
            return None
        return self.base_time_min + self.alert_extra_time_min + self.sim_added_time_min


@dataclass
class Graph:
    hubs: dict[str, Hub]
    adj: dict[str, list[GraphEdge]]
    session: Session
    scenario: str                      # fare scenario in effect
    time_scenario: str
    closed: dict[str, str] = field(default_factory=dict)         # route_id -> reason
    delayed: dict[str, float] = field(default_factory=dict)      # route_id -> minutes from alerts
    sim_effects: dict[str, Any] = field(default_factory=dict)
    sim_scenario_id: Optional[str] = None
    zone_hub: Optional[str] = None
    transfer_wait_min: float = 0.0
    warnings: list[str] = field(default_factory=list)
    _fares: dict[str, Optional[LegFareResult]] = field(default_factory=dict)

    @property
    def edges(self) -> list[GraphEdge]:
        return [ge for lst in self.adj.values() for ge in lst]

    @property
    def has_all_coordinates(self) -> bool:
        nodes = {ge.from_hub for ge in self.edges} | {ge.to_hub for ge in self.edges}
        return bool(nodes) and all(_has_coords(self.hubs.get(n)) for n in nodes)

    def leg_fare(self, ge: GraphEdge) -> Optional[LegFareResult]:
        """Edge fare (regular + student) in the scenario in effect; None when the dataset has none."""
        if ge.route_id not in self._fares:
            try:
                self._fares[ge.route_id] = fs.calculate_leg_fare(self.session, ge.edge, self.scenario, True)
            except FareError:
                self._fares[ge.route_id] = None
        return self._fares[ge.route_id]

    def fare_min(self, ge: GraphEdge, prefer_student: bool) -> Optional[float]:
        leg = self.leg_fare(ge)
        if leg is None:
            return None
        return leg.student_fare_min_bdt if prefer_student else leg.regular_fare_min_bdt


def _norm_scenario_effects(effects: dict) -> dict:
    out = dict(effects or {})
    for key in ("fare_scenario", "time_scenario"):
        if out.get(key):
            out[key] = fs.normalize_scenario(out[key])
    return out


def load_active_simulation(session: Session, session_id: str | None, now: datetime | None = None
                           ) -> tuple[Simulation | None, dict]:
    """The session's active, unexpired simulation and its effects: those derived from its scenario_type
    (simulation_service.get_scenario_effects), overridden by any explicit keys in effects_json."""
    from app.services import simulation_service as sims

    sim = sims.get_active_scenario(session, session_id, now)
    if sim is None:
        return None, {}
    derived = sims.graph_effects(sims.compute_effects(session, sim.scenario_type, sim.zone_hub))
    return sim, _norm_scenario_effects({**derived, **(sim.effects_json or {})})


def load_active_alerts(session: Session, now: datetime | None = None) -> list[Report]:
    """Verified, unexpired reports with confidence >= 0.40 and a hub/routes: the only alerts that count anywhere."""
    now = now or utcnow()
    candidates = session.exec(select(Report).where(
        Report.status == ReportStatus.verified.value, Report.expires_at > now).order_by(Report.report_id)).all()
    # live check (confidence decays with time): same rule as alert_service.get_routing_impacting_alerts
    return [r for r in candidates if alert_rules.affects_routing(r, now)]


def resolve_fare_scenario(requested: str, effects: dict) -> tuple[str, Optional[str]]:
    """(fare scenario in effect, warning). The simulation's fare scenario applies unless the caller asked
    for a specific non-normal scenario."""
    requested = fs.normalize_scenario(requested)
    sim_fare = effects.get("fare_scenario")
    if sim_fare and requested == "normal":
        return sim_fare, None
    if sim_fare and sim_fare != requested:
        return requested, f"SCENARIO_CONFLICT: requested '{requested}' overrides the simulation's '{sim_fare}'."
    return requested, None


def load_alert_effects(session: Session, now: datetime | None = None) -> tuple[dict[str, list[str]], dict[str, float]]:
    """(closed route_id -> report ids, route_id -> extra minutes) from VERIFIED, unexpired reports only.
    Draft / pending / rejected / resolved / expired reports never affect routing. Overlapping delays on the
    same edge take the largest value (duplicate reports must not stack)."""
    closed: dict[str, list[str]] = {}
    delay: dict[str, float] = {}
    for r in load_active_alerts(session, now):
        for rid in r.affected_route_ids_json or []:
            if r.edge_closed:
                closed.setdefault(rid, []).append(r.report_id)
            if r.extra_time_min and r.extra_time_min > 0:
                delay[rid] = max(delay.get(rid, 0.0), float(r.extra_time_min))
    return closed, delay


def _scenario_time(edge: RouteEdge, time_scenario: str, rates: dict[str, ModeRate], hubs: dict[str, Hub]
                   ) -> tuple[Optional[float], list[str]]:
    """Dataset travel time for the scenario; else derived from mode speed x distance; else None."""
    warnings: list[str] = []
    column = CONFIG.get("scenario_time_columns", {}).get(time_scenario)
    value = getattr(edge, column) if column else None
    if value is not None:
        return float(value), warnings
    if edge.time_normal_min is not None:
        if time_scenario != "normal":
            code = {"rain": "TIME_RAIN_DATA_MISSING", "peak": "TIME_PEAK_DATA_MISSING",
                    "detour": "TIME_DETOUR_DATA_MISSING"}.get(time_scenario, "TIME_NOT_SCENARIO_SPECIFIC")
            warnings.append(f"{code}: no dataset '{time_scenario}' time for this route; normal time used.")
        return float(edge.time_normal_min), warnings
    rate = rates.get(edge.mode)
    if rate is not None and rate.average_speed_kmh:
        try:
            km = fs.calculate_distance_km(edge, hubs.get(edge.origin_hub), hubs.get(edge.destination_hub))
        except FareError:
            return None, ["TIME_UNKNOWN: no dataset time and no distance to derive one."]
        minutes = km / rate.average_speed_kmh * 60
        warnings.append(f"TIME_DERIVED: {km:.2f} km at the dataset's {rate.average_speed_kmh:g} km/h "
                        f"average for {edge.mode}.")
        return minutes, warnings
    return None, ["TIME_UNKNOWN: no dataset time and no mode speed to derive one."]


def build_graph(session: Session, session_id: str | None = None, avoid_hubs: list[str] | None = None,
                avoid_modes: list[str] | None = None, *, scenario: str = "normal",
                keep_hubs: tuple[str, ...] = (), now: datetime | None = None) -> Graph:
    """Hubs -> nodes, route_edges -> directed edges, with closures/delays/simulation applied (read-only).

    avoid_hubs / avoid_modes are canonical ids; hubs in keep_hubs (origin, destination) are never avoided.
    """
    now = now or utcnow()
    avoid_hubs = set(avoid_hubs or []) - set(keep_hubs)
    avoid_modes = set(avoid_modes or [])
    hubs = {h.hub_id: h for h in session.exec(select(Hub)).all()}
    rates = {r.mode: r for r in session.exec(select(ModeRate)).all()}
    alert_closed, alert_delay = load_alert_effects(session, now)
    sim, effects = load_active_simulation(session, session_id, now)

    fare_scenario, conflict = resolve_fare_scenario(scenario, effects)
    warnings: list[str] = [conflict] if conflict else []
    time_scenario = effects.get("time_scenario") or fare_scenario

    zone = effects.get("zone_hub") or (sim.zone_hub if sim else None)
    graph = Graph(hubs=hubs, adj={}, session=session, scenario=fare_scenario, time_scenario=time_scenario,
                  sim_effects=effects, sim_scenario_id=sim.scenario_id if sim else None, zone_hub=zone,
                  transfer_wait_min=float(effects.get("transfer_wait_min", CONFIG.get("transfer_wait_min", 0)) or 0),
                  delayed=dict(alert_delay), warnings=warnings)
    rainy = fare_scenario in ("rain", "peak_plus_rain", "detour") or time_scenario == "rain"
    time_mult = float(effects.get("time_multiplier", 1.0) or 1.0)
    added = float(effects.get("added_time_min", 0.0) or 0.0)

    def in_zone(e: RouteEdge) -> bool:
        return zone is None or zone in (e.origin_hub, e.destination_hub)

    all_edges = sorted(session.exec(select(RouteEdge)).all(), key=lambda e: e.route_id)
    for edge in all_edges:
        if edge.mode in avoid_modes or edge.origin_hub in avoid_hubs or edge.destination_hub in avoid_hubs:
            continue
        reason = None
        if edge.status == "closed":
            reason = f"admin: {edge.closed_reason or 'status closed'}"
        elif edge.route_id in alert_closed:
            reason = f"verified report(s) {', '.join(alert_closed[edge.route_id])}"
        elif edge.route_id in effects.get("closed_route_ids", {}):
            reason = f"simulation {sim.scenario_id}: {effects['closed_route_ids'][edge.route_id]}"
        elif effects.get("close_zone_edges") and zone and edge.mode != WALK and zone in (edge.origin_hub, edge.destination_hub):
            reason = f"simulation {sim.scenario_id}: zone {zone} closed"
        elif effects.get("close_flood_prone") and edge.flood_prone and in_zone(edge):
            reason = f"simulation {sim.scenario_id}: flood-prone edge closed"
        elif effects.get("close_construction_prone") and edge.construction_prone and in_zone(edge):
            reason = f"simulation {sim.scenario_id}: construction-prone edge closed"
        if reason:
            graph.closed[edge.route_id] = reason
            continue

        base, tw = _scenario_time(edge, time_scenario, rates, hubs)
        sim_here = bool(sim) and in_zone(edge)
        if base is not None and sim_here and time_mult != 1.0:
            base *= time_mult
        sim_added = added if (sim_here and base is not None) else 0.0
        extra = alert_delay.get(edge.route_id, 0.0) if base is not None else 0.0
        risk = 0.0
        if extra > 0:
            risk += 1
        if sim_here and (time_mult > 1.0 or sim_added > 0):
            risk += 1
        if (edge.flood_prone or edge.construction_prone) and rainy:
            risk += 1

        directions = [(edge.origin_hub, edge.destination_hub)]
        extra_warning = None
        if edge.direction == "bidirectional":
            directions.append((edge.destination_hub, edge.origin_hub))
        elif edge.direction == "unknown":
            if edge.source_dataset in SYMMETRIC_SOURCES:
                directions.append((edge.destination_hub, edge.origin_hub))
            elif ASSUME_REVERSE_UNKNOWN:
                directions.append((edge.destination_hub, edge.origin_hub))
                extra_warning = ("DIRECTION_ASSUMED: the dataset gives no direction for this route; "
                                 "treated as two-way.")
            else:
                extra_warning = ("DIRECTION_UNKNOWN: the dataset gives no direction for this route; "
                                 "traversed origin->destination only.")
        for a, b in directions:
            ge = GraphEdge(route_id=edge.route_id, mode=edge.mode, from_hub=a, to_hub=b, edge=edge,
                           base_time_min=base, alert_extra_time_min=extra, sim_added_time_min=sim_added,
                           risk_points=risk, warnings=list(tw) + ([extra_warning] if extra_warning else []))
            if extra > 0:
                ge.warnings.append(f"DELAY_REPORTED: verified report adds {extra:g} min.")
            if sim_added > 0 or (sim_here and time_mult != 1.0):
                ge.warnings.append("SIMULATION_DELAY: session simulation slows this leg.")
            graph.adj.setdefault(a, []).append(ge)
    for lst in graph.adj.values():
        lst.sort(key=lambda g: (g.route_id, g.to_hub))
    if effects.get("close_flood_prone") or effects.get("close_construction_prone"):
        flagged_flood = sum(1 for e in all_edges if e.flood_prone)
        flagged_construction = sum(1 for e in all_edges if e.construction_prone)
        if effects.get("close_flood_prone") and not flagged_flood:
            graph.warnings.append("SIMULATION_NO_EFFECT: the dataset flags no flood-prone edges, so none were closed.")
        if effects.get("close_construction_prone") and not flagged_construction:
            graph.warnings.append("SIMULATION_NO_EFFECT: the dataset flags no construction-prone edges, so none were closed.")
    return graph


# ============================================================================= search
@dataclass
class SearchContext:
    prefer_student: bool = False
    penalties: Counter = field(default_factory=Counter)  # route_id -> times already used (alternates)
    max_fare: float = 1.0
    max_time: float = 1.0


@dataclass
class PathResult:
    edges: list[GraphEdge]
    cost: float
    objective: str
    algorithm: str  # "a_star" or "dijkstra"
    expanded: int = 0


def make_context(graph: Graph, prefer_student: bool) -> SearchContext:
    ctx = SearchContext(prefer_student=prefer_student)
    fares = [f for f in (graph.fare_min(ge, prefer_student) for ge in graph.edges) if f is not None]
    times = [t for t in (ge.time_min for ge in graph.edges) if t is not None]
    ctx.max_fare = max(fares) if fares and max(fares) > 0 else 1.0
    ctx.max_time = max(times) if times and max(times) > 0 else 1.0
    return ctx


def _mode_changes(prev_mode: Optional[str], ge: GraphEdge) -> bool:
    return prev_mode is not None and ge.mode != WALK and ge.mode != prev_mode


def edge_cost(graph: Graph, ctx: SearchContext, objective: str, ge: GraphEdge, prev_mode: Optional[str]
              ) -> Optional[float]:
    """Cost of traversing `ge` after a leg in `prev_mode`; None excludes the edge for this objective."""
    changed = _mode_changes(prev_mode, ge)
    wait = graph.transfer_wait_min if changed else 0.0
    t = ge.time_min
    fare = graph.fare_min(ge, ctx.prefer_student)
    if objective == "cheapest":
        if fare is None:
            return None
        cost = fare + 1e-6 * (t or 0.0)
    elif objective == "fastest":
        if t is None:
            return None
        cost = t + wait
    elif objective == "fewest_transfers":
        # Tiebreaks only (never reported as time): fewer legs first, and an unknown time is not "free".
        cost = ((TRANSFER_PENALTY if changed else 0) + (t if t is not None else UNKNOWN_TIME_TIEBREAK_MIN)
                + wait + LEG_TIEBREAK + 1e-6 * (fare or 0.0))
    elif objective == "balanced":
        if t is None or fare is None:
            return None
        cost = (WEIGHTS["fare"] * fare / ctx.max_fare + WEIGHTS["time"] * (t + wait) / ctx.max_time
                + WEIGHTS["transfer"] * (1 if changed else 0) + WEIGHTS["risk_per_point"] * ge.risk_points)
    else:
        raise ValueError(f"unknown objective {objective!r}")
    k = ctx.penalties.get(ge.route_id, 0)
    return cost * (1 + k) + k  # alternates: previously used edges get costlier


def _tracks_mode(graph: Graph, objective: str) -> bool:
    return objective in ("fewest_transfers", "balanced") or (objective == "fastest" and graph.transfer_wait_min > 0)


def _max_speed_km_per_min(graph: Graph, ctx: SearchContext) -> float:
    """Highest straight-line speed any edge achieves; dividing distance by it never overestimates time."""
    best = 0.0
    for ge in graph.edges:
        t = ge.time_min
        if t and t > 0:
            best = max(best, _hub_distance_km(graph.hubs[ge.from_hub], graph.hubs[ge.to_hub]) / t)
    return best


def _search(graph: Graph, start: str, goal: str, objective: str, ctx: SearchContext, use_heuristic: bool
            ) -> Optional[PathResult]:
    if start == goal or start not in graph.adj:
        return None
    heuristic = lambda node: 0.0  # noqa: E731
    algorithm = "dijkstra"
    if use_heuristic and objective == "fastest" and graph.has_all_coordinates and (
            _has_coords(graph.hubs.get(start)) and _has_coords(graph.hubs.get(goal))):
        vmax = _max_speed_km_per_min(graph, ctx)
        if vmax > 0 and goal in graph.hubs:
            g_hub = graph.hubs[goal]
            heuristic = lambda node: _hub_distance_km(graph.hubs[node], g_hub) / vmax  # noqa: E731
            algorithm = "a_star"
    track = _tracks_mode(graph, objective)
    counter = itertools.count()
    start_state = (start, None)
    best = {start_state: 0.0}
    parent: dict[tuple, tuple] = {}
    heap = [(heuristic(start), next(counter), 0.0, start_state)]
    expanded = 0
    while heap:
        _, _, g, state = heapq.heappop(heap)
        if g > best.get(state, math.inf) + 1e-12:
            continue
        node, last = state
        if node == goal:
            edges: list[GraphEdge] = []
            while state in parent:
                state, ge = parent[state]
                edges.append(ge)
            edges.reverse()
            return PathResult(edges, g, objective, algorithm, expanded)
        expanded += 1
        for ge in graph.adj.get(node, []):
            c = edge_cost(graph, ctx, objective, ge, last)
            if c is None:
                continue
            new_last = (last if ge.mode == WALK else ge.mode) if track else None
            nstate = (ge.to_hub, new_last)
            ng = g + c
            if ng < best.get(nstate, math.inf) - 1e-12:
                best[nstate] = ng
                parent[nstate] = (state, ge)
                heapq.heappush(heap, (ng + heuristic(ge.to_hub), next(counter), ng, nstate))
    return None


def dijkstra(graph: Graph, start: str, goal: str, objective: str, session_context: SearchContext
             ) -> Optional[PathResult]:
    return _search(graph, start, goal, objective, session_context, use_heuristic=False)


def a_star(graph: Graph, start: str, goal: str, objective: str, session_context: SearchContext
           ) -> Optional[PathResult]:
    """A* with a Haversine heuristic for the time objective when EVERY hub has coordinates; otherwise
    (or for non-time objectives) the heuristic is 0, i.e. Dijkstra. PathResult.algorithm says which ran."""
    return _search(graph, start, goal, objective, session_context, use_heuristic=True)


# ============================================================================= hub resolution
@dataclass
class HubResolution:
    hub: Optional[Hub] = None
    code: Optional[str] = None  # None | AMBIGUOUS_HUB | UNKNOWN_HUB
    message: str = ""
    matched_by: Optional[str] = None
    candidates: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)


def _cand(h: Hub, matched_by: str | None = None) -> dict:
    return {"hub_id": h.hub_id, "name": h.name, **({"matched_by": matched_by} if matched_by else {})}


def resolve_hub(session: Session, text: str) -> HubResolution:
    """exact hub_id -> canonical name -> alias -> fuzzy (rapidfuzz if installed, else containment/difflib)."""
    query = (text or "").strip()
    if not query:
        return HubResolution(code="UNKNOWN_HUB", message="No hub name given.")
    hubs = session.exec(select(Hub).order_by(Hub.hub_id)).all()
    if not hubs:
        return HubResolution(code="UNKNOWN_HUB", message="There are no hubs in the database.")
    by_id = {h.hub_id: h for h in hubs}
    qid, qkey = make_hub_id(query), compact_key(query)

    if query in by_id or qid in by_id:
        return HubResolution(hub=by_id.get(query) or by_id[qid], matched_by="hub_id")
    named = [h for h in hubs if compact_key(h.name) == qkey]
    if len(named) == 1:
        return HubResolution(hub=named[0], matched_by="name")
    if len(named) > 1:
        return HubResolution(code="AMBIGUOUS_HUB", message=f"'{text}' matches several hubs.",
                             candidates=[_cand(h, "name") for h in named])
    aliased = [h for h in hubs if any(compact_key(a) == qkey for a in (h.aliases_json or []))]
    if len(aliased) == 1:
        return HubResolution(hub=aliased[0], matched_by="alias")
    if len(aliased) > 1:
        return HubResolution(code="AMBIGUOUS_HUB", message=f"'{text}' is an alias of several hubs.",
                             candidates=[_cand(h, "alias") for h in aliased])

    keys = {h.hub_id: [compact_key(h.name)] + [compact_key(a) for a in (h.aliases_json or [])] for h in hubs}
    matches: list[tuple[Hub, float]] = []
    try:  # optional dependency
        from rapidfuzz import fuzz, process

        choices = {f"{h.hub_id}|{i}": k for h, ks in ((by_id[i], keys[i]) for i in keys) for i, k in enumerate(ks)}
        scored = process.extract(qkey, choices, scorer=fuzz.WRatio, limit=8, score_cutoff=85)
        seen: dict[str, float] = {}
        for _, score, label in scored:
            seen[label.split("|")[0]] = max(seen.get(label.split("|")[0], 0), score / 100)
        matches = [(by_id[i], s) for i, s in seen.items()]
    except ImportError:
        for h in hubs:
            score = 0.0
            for k in keys[h.hub_id]:
                if len(qkey) >= 3 and (qkey in k or (len(k) >= 3 and k in qkey)):
                    score = max(score, 0.9)
                score = max(score, SequenceMatcher(None, qkey, k).ratio() if len(qkey) >= 4 else 0)
            if score >= 0.85:
                matches.append((h, score))
    matches.sort(key=lambda m: (-m[1], m[0].hub_id))
    if len(matches) == 1 or (len(matches) > 1 and matches[0][1] - matches[1][1] >= 0.08):
        return HubResolution(hub=matches[0][0], matched_by="fuzzy",
                             message=f"'{text}' matched '{matches[0][0].name}' approximately.")
    if matches:
        return HubResolution(code="AMBIGUOUS_HUB", message=f"'{text}' could mean several hubs.",
                             candidates=[_cand(h, "fuzzy") for h, _ in matches[:5]])
    names = {h.name: h for h in hubs}
    close = get_close_matches(query, list(names), n=5, cutoff=0.5)
    return HubResolution(code="UNKNOWN_HUB", message=f"No hub matches '{text}'.",
                         suggestions=[_cand(names[n]) for n in close])


# ============================================================================= route option
def _price_path(session: Session, graph: Graph, path: list[GraphEdge]) -> list[Optional[LegFareResult]]:
    """Per-leg fares. Consecutive metro legs are one ride, priced once from the official matrix
    (first leg carries the fare, the rest 0); unpriceable legs are None."""
    out: list[Optional[LegFareResult]] = [None] * len(path)
    i = 0
    while i < len(path):
        if path[i].mode != METRO:
            out[i] = graph.leg_fare(path[i])
            i += 1
            continue
        j = i
        while j + 1 < len(path) and path[j + 1].mode == METRO:
            j += 1
        priced = False
        if j > i:
            try:
                ride = fs.calculate_metro_fare(session, path[i].from_hub, path[j].to_hub)
                student, applies = fs.calculate_student_fare(session, METRO, ride, "normal", None)
                for k in range(i, j + 1):
                    first = k == i
                    out[k] = LegFareResult(
                        route_id=path[k].route_id, mode=METRO,
                        regular_fare_min_bdt=ride.min_bdt if first else 0, regular_fare_max_bdt=ride.max_bdt if first else 0,
                        student_fare_min_bdt=student.min_bdt if first else 0, student_fare_max_bdt=student.max_bdt if first else 0,
                        student_discount_applies=applies and first, scenario=graph.scenario, distance_km=None,
                        fare_source="metro_matrix",
                        warnings=[] if first else [f"METRO_RIDE: fare is included in the {path[i].from_hub}"
                                                   f"->{path[j].to_hub} ride charged on the first metro leg."])
                priced = True
            except FareError:
                priced = False
        if not priced:
            for k in range(i, j + 1):
                out[k] = graph.leg_fare(path[k])
        i = j + 1
    return out


def _risk_label(points: float) -> str:
    levels = CONFIG.get("risk_levels", {})
    if points >= levels.get("high_from_points", 2):
        return "high"
    if points >= levels.get("medium_from_points", 0.5):
        return "medium"
    return "low"


def _first_note(notes: Any) -> Optional[str]:
    if isinstance(notes, list) and notes:
        return "; ".join(str(n) for n in notes)
    if isinstance(notes, str) and notes:
        return notes
    return None


def calculate_route_option(session: Session, graph: Graph, path_edges: list[GraphEdge], scenario: str = "normal",
                           prefer_student: bool = False, rank_label: str = "balanced", index: int = 1,
                           budget_bdt: int | None = None, also_best_for: list[str] | None = None) -> RouteOption:
    fare_scenario = graph.scenario
    leg_fares = _price_path(session, graph, path_edges)
    priced = all(f is not None for f in leg_fares)
    trip = fs.calculate_trip_fare([f for f in leg_fares if f is not None], prefer_student=True) if priced else None

    # transfers: mode changes between consecutive non-walk legs; wait attaches to the leg before the change
    vehicle = [i for i, ge in enumerate(path_edges) if ge.mode != WALK]
    wait_after = [0.0] * len(path_edges)
    boundary_after: set[int] = set()
    transfers = 0
    for a, b in zip(vehicle, vehicle[1:]):
        if path_edges[a].mode != path_edges[b].mode:
            transfers += 1
            boundary_after.add(a)
            wait_after[a] = graph.transfer_wait_min

    legs: list[RouteLeg] = []
    for n, (ge, lf) in enumerate(zip(path_edges, leg_fares), start=1):
        t = ge.time_min
        warnings = list(ge.warnings) + (list(lf.warnings) if lf else
                                         ["FARE_UNKNOWN: the dataset has no fare for this leg."])
        legs.append(RouteLeg(
            order=n, mode=ge.mode, route_id=ge.route_id, from_hub=ge.from_hub, to_hub=ge.to_hub,
            regular_fare_min_bdt=lf.regular_fare_min_bdt if lf else None,
            regular_fare_max_bdt=lf.regular_fare_max_bdt if lf else None,
            student_fare_min_bdt=lf.student_fare_min_bdt if lf else None,
            student_fare_max_bdt=lf.student_fare_max_bdt if lf else None,
            student_discount_applies=lf.student_discount_applies if lf else False,
            travel_time_min=t, transfer_wait_after_min=wait_after[n - 1],
            walking_time_min=(t or 0.0) if ge.mode == WALK else 0.0,
            boarding_instruction=_first_note(ge.edge.boarding_notes_json),
            alighting_instruction=_first_note(ge.edge.alighting_notes_json),
            transfer_instruction=_first_note(ge.edge.transfer_notes_json) if n - 1 in boundary_after else None,
            warnings=warnings))

    times = [l.travel_time_min for l in legs]
    travel = None if any(t is None for t in times) else sum(times)
    wait_total = sum(wait_after)
    walking = sum(l.walking_time_min for l in legs)
    # Walk legs are already inside travel_time_min (reported again in walking_time_min for transparency),
    # so they are not added a second time to the duration.
    duration = None if travel is None else travel + wait_total

    option_warnings: list[str] = []
    for l in legs:
        for w in l.warnings:
            if w not in option_warnings:
                option_warnings.append(w)
    if trip:
        for w in trip.warnings:
            if "estimates" in w and w not in option_warnings:
                option_warnings.append(w)
    if not priced:
        option_warnings.append("FARE_UNKNOWN: totals unavailable because at least one leg has no dataset fare.")
    if travel is None:
        option_warnings.append("TIME_UNKNOWN: totals unavailable because at least one leg has no travel time.")

    reg_min = trip.total_regular_fare_min_bdt if trip else None
    stu_min = trip.total_student_fare_min_bdt if trip else None
    within = True
    if budget_bdt is not None:
        compare = stu_min if prefer_student else reg_min
        within = compare is not None and compare <= budget_bdt
        if compare is None:
            option_warnings.append("BUDGET_UNVERIFIABLE: fare unknown, cannot confirm the route fits the budget.")
        elif not within:
            option_warnings.append(f"OVER_BUDGET: minimum fare {compare:.2f} BDT exceeds the {budget_bdt} BDT budget.")

    ids_ok = all(session.get(RouteEdge, ge.route_id) is not None for ge in path_edges)
    hubs_ok = all(session.get(Hub, h) is not None for ge in path_edges for h in (ge.from_hub, ge.to_hub))
    contiguous = all(a.to_hub == b.from_hub for a, b in zip(path_edges, path_edges[1:]))
    return RouteOption(
        route_option_id=f"OPT_{rank_label.upper()}_{index:03d}", rank_label=rank_label,
        also_best_for=also_best_for or [],
        total_regular_fare_min_bdt=reg_min, total_regular_fare_max_bdt=trip.total_regular_fare_max_bdt if trip else None,
        total_student_fare_min_bdt=stu_min, total_student_fare_max_bdt=trip.total_student_fare_max_bdt if trip else None,
        total_travel_time_min=travel, total_transfer_wait_min=wait_total, total_walking_time_min=walking,
        total_duration_min=duration, transfers=transfers, leg_count=len(legs), within_budget=within,
        disruption_risk=_risk_label(sum(ge.risk_points for ge in path_edges)), scenario=fare_scenario,
        legs=legs, warnings=option_warnings,
        grounding=RouteGrounding(valid=ids_ok and hubs_ok and contiguous and priced, checked_route_ids=ids_ok,
                                 checked_hub_ids=hubs_ok, checked_fares=priced),
    )


# ============================================================================= multi-route search
def _sequence(path: PathResult) -> tuple[str, ...]:
    return tuple(ge.route_id for ge in path.edges)


def _sort_key(opt: RouteOption, objective: str, prefer_student: bool):
    fare = opt.total_student_fare_min_bdt if prefer_student else opt.total_regular_fare_min_bdt
    fare = math.inf if fare is None else fare
    time = math.inf if opt.total_duration_min is None else opt.total_duration_min
    if objective == "cheapest":
        return (fare, time, opt.transfers)
    if objective == "fastest":
        return (time, fare, opt.transfers)
    if objective == "fewest_transfers":
        return (opt.transfers, time, fare)
    return (fare / 100 + time / 60 + opt.transfers, fare, time)


def ensure_minimum_routes(session: Session, graph: Graph, ctx: SearchContext, start: str, goal: str,
                          found: dict[tuple[str, ...], PathResult], optimize: str, min_routes: int = MIN_ROUTES,
                          ) -> dict[tuple[str, ...], PathResult]:
    # `optimize` is the objective used for alternates (a balanced request that fell back uses cheapest)
    """Add unique alternatives by re-searching with already-used edges penalized."""
    ctx.penalties = Counter()
    for path in found.values():
        for ge in path.edges:
            ctx.penalties[ge.route_id] += 1
    attempts = 0
    while len(found) < min_routes and attempts < MAX_ALTERNATE_ATTEMPTS:
        attempts += 1
        path = a_star(graph, start, goal, optimize, ctx)
        if path is None:
            break
        seq = _sequence(path)
        for ge in path.edges:  # escalate so the next attempt looks elsewhere
            ctx.penalties[ge.route_id] += 1
        if seq not in found:
            path.objective = "alternative"
            found[seq] = path
    ctx.penalties = Counter()
    return found


def _search_all(session: Session, graph: Graph, ctx: SearchContext, start: str, goal: str, optimize: str
                ) -> tuple[dict[tuple[str, ...], PathResult], dict[tuple[str, ...], list[str]], list[str]]:
    """Best path per objective (deduped by route_id sequence), then penalized alternates."""
    found: dict[tuple[str, ...], PathResult] = {}
    best_for: dict[tuple[str, ...], list[str]] = {}
    notes: list[str] = []
    order = [optimize] + [o for o in OBJECTIVES if o != optimize]
    effective = optimize
    for objective in order:
        path = a_star(graph, start, goal, objective, ctx)
        if path is None and objective == "balanced":
            path = a_star(graph, start, goal, "cheapest", ctx)
            if path is not None:
                effective = "cheapest"
                notes.append("BALANCED_FELL_BACK_TO_CHEAPEST: not enough time/fare data for a balanced ranking.")
                path.objective = "balanced"
        if path is None:
            continue
        seq = _sequence(path)
        best_for.setdefault(seq, []).append(objective)
        found.setdefault(seq, path)
    found = ensure_minimum_routes(session, graph, ctx, start, goal, found, effective)
    return found, best_for, notes


# ============================================================================= entry point
def _error(code: str, message: str, **extra) -> dict:
    return {"routes": [], "error": {"code": code, "message": message, **extra}, "warnings": [], "warning_codes": []}


def search_routes(session: Session, origin_text: str, destination_text: str, budget_bdt: int | None = None,
                  strict_budget: bool = False, prefer_student_fare: bool = False,
                  avoid_hubs: list[str] | None = None, avoid_modes: list[str] | None = None,
                  optimize: str = "balanced", session_id: str | None = None, scenario: str = "normal") -> dict:
    if optimize not in OBJECTIVES:
        return _error("INVALID_OPTIMIZE", f"optimize must be one of {list(OBJECTIVES)}.", allowed=list(OBJECTIVES))
    try:
        scenario = fs.normalize_scenario(scenario)
    except FareError as exc:
        return _error(exc.code, exc.message, **(exc.details or {}))

    resolved = {}
    for field_name, text in (("origin", origin_text), ("destination", destination_text)):
        r = resolve_hub(session, text)
        if r.code:
            return _error(r.code, r.message, field=field_name, candidates=r.candidates, suggestions=r.suggestions)
        resolved[field_name] = r
    origin, destination = resolved["origin"].hub, resolved["destination"].hub
    if origin.hub_id == destination.hub_id:
        return _error("SAME_ORIGIN_DESTINATION", "Origin and destination are the same hub.", hub_id=origin.hub_id)

    warnings: list[str] = []
    for field_name, r in resolved.items():
        if r.matched_by == "fuzzy":
            warnings.append(f"HUB_FUZZY_MATCH: {r.message}")
    avoid_ids: list[str] = []
    for text in avoid_hubs or []:
        r = resolve_hub(session, text)
        if r.hub is None:
            warnings.append(f"AVOID_HUB_IGNORED: '{text}' {('is ambiguous' if r.code == 'AMBIGUOUS_HUB' else 'is unknown')}.")
        elif r.hub.hub_id in (origin.hub_id, destination.hub_id):
            warnings.append(f"AVOID_HUB_IGNORED: {r.hub.hub_id} is the origin or destination.")
        else:
            avoid_ids.append(r.hub.hub_id)
    modes: list[str] = []
    for m in avoid_modes or []:
        if m in values(TransitMode):
            modes.append(m)
        else:
            warnings.append(f"AVOID_MODE_IGNORED: '{m}' is not a known mode.")

    def run(avoid_h: list[str], avoid_m: list[str]):
        graph = build_graph(session, session_id, avoid_h, avoid_m, scenario=scenario,
                            keep_hubs=(origin.hub_id, destination.hub_id))
        ctx = make_context(graph, prefer_student_fare)
        return graph, ctx, *_search_all(session, graph, ctx, origin.hub_id, destination.hub_id, optimize)

    graph, ctx, found, best_for, notes = run(avoid_ids, modes)
    warnings += graph.warnings + notes
    disruptions = {
        "closed_routes": [{"route_id": r, "reason": why} for r, why in sorted(graph.closed.items())],
        "delayed_routes": [{"route_id": r, "extra_time_min": m} for r, m in sorted(graph.delayed.items())],
        "simulation_scenario_id": graph.sim_scenario_id,
    }

    options: list[RouteOption] = []
    for seq, path in found.items():
        options.append(calculate_route_option(
            session, graph, path.edges, scenario, prefer_student_fare,
            rank_label=path.objective if path.objective != "alternative" else "alternative",
            budget_bdt=budget_bdt, also_best_for=[o for o in best_for.get(seq, [])]))
    # label each option with the first objective it won; objectives it also won go to also_best_for
    for opt, (seq, path) in zip(options, found.items()):
        won = best_for.get(seq, [])
        if won:
            opt.rank_label = won[0]
            opt.also_best_for = won[1:]
    options.sort(key=lambda o: (0 if o.rank_label == optimize else 1 if o.rank_label in OBJECTIVES else 2,
                                OBJECTIVES.index(o.rank_label) if o.rank_label in OBJECTIVES else 99,
                                _sort_key(o, optimize, prefer_student_fare)))
    unpriced = [o for o in options if o.total_regular_fare_min_bdt is None]
    if unpriced and len(options) - len(unpriced) >= MIN_ROUTES:
        options = [o for o in options if o.total_regular_fare_min_bdt is not None]
        warnings.append(f"ROUTES_OMITTED_FARE_UNKNOWN: {len(unpriced)} route(s) without a dataset fare were left out.")
    options.sort(key=lambda o: o.total_regular_fare_min_bdt is None)  # stable: unpriced routes last
    if strict_budget and budget_bdt is not None:
        dropped = [o for o in options if not o.within_budget]
        options = [o for o in options if o.within_budget]
    else:
        dropped = []
    options = options[:MAX_ROUTES]
    for n, opt in enumerate(options, start=1):
        opt.route_option_id = f"OPT_{opt.rank_label.upper()}_{n:03d}"

    result: dict[str, Any] = {
        "routes": [], "origin": _cand(origin), "destination": _cand(destination), "optimize": optimize,
        "scenario": graph.scenario, "prefer_student_fare": prefer_student_fare, "budget_bdt": budget_bdt,
        "strict_budget": strict_budget, "session_id": session_id, "disruptions": disruptions,
        "warnings": warnings, "warning_codes": [],
    }
    if options:
        if len(options) < MIN_ROUTES:
            warnings.append(f"LESS_THAN_THREE_ROUTES_AVAILABLE: only {len(options)} unique route(s) exist "
                            "within the constraints.")
        result["routes"] = [o.model_dump() for o in options]
    else:
        result["no_route_reason"] = _no_route_reason(
            session, run, avoid_ids, modes, dropped, budget_bdt, strict_budget, optimize, prefer_student_fare,
            graph, origin.hub_id, destination.hub_id).model_dump()
        warnings.append(f"{result['no_route_reason']['code']}: {result['no_route_reason']['message']}")
    result["warning_codes"] = sorted({w.split(":", 1)[0] for w in warnings if re.match(r"^[A-Z_]{4,}:", w)})
    return result


def _no_route_reason(session, run, avoid_ids, modes, dropped, budget_bdt, strict_budget, optimize,
                     prefer_student, graph, start, goal) -> NoRouteReason:
    suggestions: list[NoRouteSuggestion] = []
    if dropped:
        fares = [(o.total_student_fare_min_bdt if prefer_student else o.total_regular_fare_min_bdt) for o in dropped]
        fares = [f for f in fares if f is not None]
        if fares:
            need = math.ceil(min(fares))
            suggestions.append(NoRouteSuggestion(
                type="increase_budget", value=need,
                message=f"Routes exist from {need} BDT; the budget is {budget_bdt} BDT (strict budget)."))
    for hub in avoid_ids:
        rest = [h for h in avoid_ids if h != hub]
        if run(rest, modes)[2]:
            suggestions.append(NoRouteSuggestion(type="remove_avoided_hub", value=hub,
                                                 message=f"A route exists if {hub} is not avoided."))
    for mode in modes:
        rest = [m for m in modes if m != mode]
        if run(avoid_ids, rest)[2]:
            suggestions.append(NoRouteSuggestion(type="remove_avoided_mode", value=mode,
                                                 message=f"A route exists if {mode} is not avoided."))
    if optimize in ("fastest", "balanced"):
        for other in ("cheapest", "fewest_transfers"):
            if a_star(graph, start, goal, other, make_context(graph, prefer_student)) is not None:
                suggestions.append(NoRouteSuggestion(
                    type="widen_time", value=other,
                    message=f"Some connections have no travel-time data; try optimize='{other}'."))
                break
    constrained = bool(avoid_ids or modes or dropped)
    if constrained or suggestions:
        return NoRouteReason(code="NO_ROUTE_WITHIN_CONSTRAINTS",
                             message="No route satisfies the given constraints.", suggestions=suggestions)
    return NoRouteReason(code="NO_ROUTE_FOUND",
                         message="The transit data has no connection between these hubs"
                                 + (" (some routes are currently closed)." if graph.closed else "."))
