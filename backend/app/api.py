import importlib
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session

from app.api_catalog import API_CATALOG
from app.api_admin import router as admin_router
from app.api_auth import router as auth_router
from app.api_chat import router as chat_router
from app.api_digitization import router as digitization_router
from app.api_grounding import router as grounding_router
from app.api_reports import router as reports_router
from app.api_rideshare import router as rideshare_router
from app.api_simulations import router as simulations_router
from app.deps import get_db
from app.enums import RouteEdgeStatus, TransitMode
from app.models import RouteEdge
from app.responses import api_error, not_implemented, success
from app.schemas import (
    ActiveScenarioOut, FareEstimateData, FareEstimateRequest, RouteOption, TripSearchData, TripSearchRequest,
)
from app.services import fare_service as fs
from app.services import routing_service as rs
from app.services import grounding_service, search_cache, transit_service
from app.services.fare_service import FareError

router = APIRouter()
router.include_router(auth_router)
router.include_router(reports_router)
router.include_router(grounding_router)
router.include_router(chat_router)
router.include_router(digitization_router)
router.include_router(simulations_router)
router.include_router(rideshare_router)
router.include_router(admin_router)

SEARCH_FIELDS = ("origin", "destination", "budget_bdt", "strict_budget", "prefer_student_fare", "avoid_hubs",
                 "avoid_modes", "optimize", "scenario")


@router.get("/health")
def health():
    return success({"status": "ok"})


@router.get("/api/catalog")
def catalog():
    return success(API_CATALOG)


# ============================================================================= transit browsing
@router.get("/api/transit/network")
def transit_network(session_id: Optional[str] = Query(None, max_length=100), include_geometry: bool = False,
                    db: Session = Depends(get_db)):
    """Hubs (derived status), routes, fare rules, mode rates, scenario multipliers.
    `session_id` makes statuses reflect that session's simulation."""
    return success(transit_service.network(db, session_id, include_geometry).model_dump(mode="json"))


@router.get("/api/transit/hubs")
def transit_hubs(session_id: Optional[str] = Query(None, max_length=100), db: Session = Depends(get_db)):
    return success([h.model_dump(mode="json") for h in transit_service.hub_statuses(db, session_id)])


@router.get("/api/transit/routes")
def transit_routes(session_id: Optional[str] = Query(None, max_length=100), mode: Optional[TransitMode] = None,
                   hub_id: Optional[str] = None, status: Optional[RouteEdgeStatus] = None,
                   include_geometry: bool = False, db: Session = Depends(get_db)):
    routes = transit_service.route_views(db, session_id, mode.value if mode else None, hub_id,
                                         status.value if status else None, include_geometry)
    return success([r.model_dump(mode="json") for r in routes])


@router.get("/api/transit/hub-statuses")
def transit_hub_statuses(session_id: Optional[str] = Query(None, max_length=100), db: Session = Depends(get_db)):
    return success({"hub_statuses": transit_service.hub_status_summaries(db, session_id)})


# ============================================================================= trip search
def _load_parse_query():
    """nlp_service.parse_query(query_text, session_id=None) -> dict of TripSearchRequest fields, or None."""
    try:
        module = importlib.import_module("app.services.nlp_service")
    except ImportError:
        return None
    return getattr(module, "parse_query", None)


def _apply_nlp(body: TripSearchRequest, db: Session) -> TripSearchRequest:
    parse_query = _load_parse_query()
    if parse_query is None:
        raise api_error(
            "NLP_NOT_IMPLEMENTED",
            "Natural-language search is not available yet. Send the structured fields instead.",
            field="query_text",
            details={"suggested_fields": {
                "origin": "Mirpur 10", "destination": "Motijheel", "budget_bdt": 80, "strict_budget": False,
                "prefer_student_fare": True, "avoid_hubs": [], "avoid_modes": [], "optimize": "balanced",
                "scenario": "normal", "session_id": body.session_id}})
    try:
        parsed = parse_query(body.query_text, session_id=body.session_id)
    except Exception as exc:  # the NLP layer must not take the endpoint down
        raise api_error("NLP_PARSE_FAILED", f"Could not understand the query: {exc}", field="query_text")
    if not isinstance(parsed, dict):
        raise api_error("NLP_PARSE_FAILED", "The query parser returned no structured result.", field="query_text")
    if parsed.get("error"):
        err = parsed["error"] if isinstance(parsed["error"], dict) else {"message": str(parsed["error"])}
        raise api_error("NLP_PARSE_FAILED", err.get("message", "Could not understand the query."),
                        field="query_text", details=err)
    merged = {k: v for k, v in parsed.items() if k in SEARCH_FIELDS and v is not None}
    for key in SEARCH_FIELDS:  # explicit structured fields beat parsed ones
        explicit = getattr(body, key)
        if key in body.model_fields_set and explicit not in (None, [], ""):
            merged[key] = explicit
    if not (merged.get("origin") and merged.get("destination")):
        raise api_error("NLP_INCOMPLETE_QUERY", "Could not find both an origin and a destination in the text.",
                        field="query_text", details={"parsed": merged})
    try:
        return TripSearchRequest(**merged, session_id=body.session_id, query_text=body.query_text)
    except ValueError as exc:
        raise api_error("NLP_PARSE_FAILED", f"The parsed query is invalid: {exc}", field="query_text")


def _check_scenario(scenario: str) -> str:
    try:
        return fs.normalize_scenario(scenario)
    except FareError as exc:
        raise api_error("INVALID_SCENARIO", exc.message, field="scenario", details=exc.details)


@router.post("/api/trips/search")
def trips_search(body: TripSearchRequest, db: Session = Depends(get_db)):
    """Structured search. `query_text` alone goes through nlp_service (not implemented yet -> 501)."""
    structured = bool(body.origin and body.destination)
    req = body if structured else _apply_nlp(body, db)
    scenario = _check_scenario(req.scenario)

    result = rs.search_routes(
        db, req.origin, req.destination, budget_bdt=req.budget_bdt, strict_budget=req.strict_budget,
        prefer_student_fare=req.prefer_student_fare, avoid_hubs=req.avoid_hubs, avoid_modes=req.avoid_modes,
        optimize=req.optimize, session_id=req.session_id, scenario=scenario)
    if result.get("error"):
        err = dict(result["error"])
        code = err.pop("code")
        message, field = err.pop("message"), err.pop("field", None)
        raise api_error({"SCENARIO_UNKNOWN": "INVALID_SCENARIO"}.get(code, code), message, field=field, details=err)

    grounding = grounding_service.validate_search_result(db, result, scenario, req.session_id)  # drops ungrounded options
    if grounding["options_dropped"]:
        result["warnings"].append(f"GROUNDING_FAILED: {grounding['options_dropped']} route option(s) failed grounding and were removed.")
    search_cache.remember_options(req.session_id, result["routes"])
    search_cache.remember_query(req.session_id, {"origin": result["origin"]["hub_id"], "destination": result["destination"]["hub_id"],
                                                 "prefer_student_fare": req.prefer_student_fare})
    sim, effects = rs.load_active_simulation(db, req.session_id)
    echo = req.model_dump(exclude_none=True)
    echo["parsed_from_text"] = not structured
    data = TripSearchData(
        query_echo=echo,
        routes=[RouteOption.model_validate(r) for r in result["routes"]],
        no_route_reason=result.get("no_route_reason"),
        session_id=req.session_id,
        active_scenario=(ActiveScenarioOut(
            scenario_id=sim.scenario_id, scenario_type=sim.scenario_type, zone_hub=sim.zone_hub, effects=effects,
            created_at=sim.created_at, expires_at=sim.expires_at) if sim else None),
        origin=result["origin"], destination=result["destination"], optimize=result["optimize"],
        scenario=result["scenario"], disruptions=result["disruptions"], warnings=result["warnings"],
        warning_codes=result["warning_codes"], grounding=grounding)
    return success(data.model_dump(mode="json"))


# ============================================================================= fare estimate
def _orient(legs: list[RouteEdge]) -> list[tuple[str, str]]:
    """(from_hub, to_hub) per leg: follow the shared hub between neighbours; the first/only leg of an
    unlinked list defaults to origin->destination."""
    out: list[tuple[str, str]] = []
    for i, e in enumerate(legs):
        ends = (e.origin_hub, e.destination_hub)
        nxt = legs[i + 1] if i + 1 < len(legs) else None
        prev_to = out[-1][1] if out else None
        if prev_to in ends:
            out.append((prev_to, ends[1] if ends[0] == prev_to else ends[0]))
        elif nxt is not None:
            shared = [h for h in ends if h in (nxt.origin_hub, nxt.destination_hub)]
            out.append((ends[1] if ends[0] == shared[0] else ends[0], shared[0]) if len(shared) == 1 else ends)
        else:
            out.append(ends)
    return out


@router.post("/api/fares/estimate")
def fares_estimate(body: FareEstimateRequest, db: Session = Depends(get_db)):
    """Price a cached route option (by id) or explicit legs. Legs are never invented."""
    scenario = _check_scenario(body.scenario)
    warnings: list[str] = []
    cached = search_cache.recall_option(body.session_id, body.route_option_id) if body.route_option_id else None
    if cached:
        legs_in = [(l["route_id"], l["mode"], l["from_hub"], l["to_hub"]) for l in cached["legs"]]
        source = "cached_route_option"
        if body.legs:
            warnings.append("LEGS_IGNORED: route_option_id was found in the recent searches, so its legs were used.")
    elif body.legs:
        legs_in, source = [(l.route_id, l.mode, None, None) for l in body.legs], "request_legs"
    else:
        raise api_error("ROUTE_OPTION_NOT_FOUND",
                        f"Route option '{body.route_option_id}' is not in the recent searches for this session; "
                        "search again or send `legs`.", field="route_option_id")

    edges: list[RouteEdge] = []
    for route_id, mode, *_ in legs_in:
        edge = db.get(RouteEdge, route_id)
        if edge is None:
            raise api_error("ROUTE_NOT_FOUND", f"Unknown route_id '{route_id}'.", field="legs",
                            details={"route_id": route_id})
        if mode and mode != edge.mode:
            raise api_error("LEG_MODE_MISMATCH", f"Route '{route_id}' is a {edge.mode} route, not {mode}.",
                            field="legs", details={"route_id": route_id, "mode": edge.mode})
        edges.append(edge)
    ends = [(f, t) for _, _, f, t in legs_in] if source == "cached_route_option" else _orient(edges)
    if source == "request_legs" and len(edges) > 1 and any(
            a[1] != b[0] for a, b in zip(ends, ends[1:])):
        warnings.append("LEGS_NOT_CONTIGUOUS: the legs do not form one connected journey; fares are summed as given.")

    _, effects = rs.load_active_simulation(db, body.session_id)
    fare_scenario, conflict = rs.resolve_fare_scenario(scenario, effects)
    if conflict:
        warnings.append(conflict)
    graph = rs.Graph(hubs={}, adj={}, session=db, scenario=fare_scenario, time_scenario=fare_scenario)
    path = [rs.GraphEdge(route_id=e.route_id, mode=e.mode, from_hub=f, to_hub=t, edge=e, base_time_min=None)
            for e, (f, t) in zip(edges, ends)]
    priced = rs._price_path(db, graph, path)
    for ge, leg in zip(path, priced):
        if leg is None:
            try:
                fs.calculate_leg_fare(db, ge.edge, fare_scenario, True)
                reason = {"code": "FARE_UNKNOWN", "message": "no fare"}
            except FareError as exc:
                reason = {"code": exc.code, "message": exc.message}
            raise api_error("FARE_CALCULATION_FAILED",
                            f"No fare could be calculated for route '{ge.route_id}': {reason['message']}",
                            field="legs", details={"route_id": ge.route_id, **reason})
    if not body.prefer_student_fare:  # student fields mirror the regular fare (fare_service convention)
        priced = [l.model_copy(update={
            "student_fare_min_bdt": l.regular_fare_min_bdt, "student_fare_max_bdt": l.regular_fare_max_bdt,
            "student_discount_applies": False}) for l in priced]
    trip = fs.calculate_trip_fare(priced, prefer_student=body.prefer_student_fare)
    trip.warnings.extend(warnings)
    data = FareEstimateData(**trip.model_dump(), route_option_id=body.route_option_id, scenario=fare_scenario,
                            prefer_student_fare=body.prefer_student_fare, session_id=body.session_id,
                            legs_source=source)
    return success(data.model_dump(mode="json"))


# Placeholders: every catalog endpoint not marked implemented answers NOT_IMPLEMENTED (HTTP 501).
_IMPLEMENTED = {(e["method"], e["path"]) for e in API_CATALOG["endpoints"] if e["implemented"]}


def _make_placeholder(path: str):
    def handler(request: Request):
        return not_implemented(f"{request.method} {path}")

    return handler


for _e in API_CATALOG["endpoints"]:
    if (_e["method"], _e["path"]) in _IMPLEMENTED:
        continue
    router.add_api_route(_e["path"], _make_placeholder(_e["path"]), methods=[_e["method"]],
                         include_in_schema=False)
