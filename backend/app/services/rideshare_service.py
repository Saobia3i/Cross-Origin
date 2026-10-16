"""Community ride-share board + equal fare-split bot (docs/RIDESHARE.md).

No payment, no booking: a post is a notice ("I'm taking a CNG from A to B, 2 seats free") and the bot shows an
equal split of an *estimated* total. Totals come only from fare_service (explicit fare row or formula); nothing is
invented. A post for a hub pair with no route edge never creates one: a transient, unsaved edge is priced instead.

Seats: `seats_taken` always equals the number of participant rows (the creator is the first one).
Every state change publishes one event in the same transaction and commits.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlmodel import Session, or_, select

from app.enums import EventType, RidesharePostStatus, TransitMode, UserRole, values
from app.models import RideshareJoin, RidesharePost, RouteEdge, User, utcnow
from app.responses import ApiError, api_error
from app.schemas import FareRange, RideShareCreate, RideShareFilters, RideShareSplit
from app.services import event_service
from app.services import fare_service as fs
from app.services import routing_service as rs

NOT_SHAREABLE_MODES = (fs.WALK, fs.METRO)  # walking is free; a metro ticket is per person, not a shared vehicle
OPEN_STATUSES = (RidesharePostStatus.active.value, RidesharePostStatus.full.value)
UPPER_BOUND_LABEL = "Upper-bound suggestion for splitting costs, not an official tariff."
COORDINATION_NOTE = "DhakaPath only shows a cost estimate; riders settle the fare among themselves."


# ----------------------------------------------------------------------------- helpers
def _iso(dt) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def require_verified(user: User | None) -> User:
    if user is None:
        raise api_error("UNAUTHORIZED", "Log in to do this. Guests can only browse ride-share posts.")
    if not user.is_verified:
        raise api_error("NOT_VERIFIED", "Only verified users can create, join, leave or cancel ride-share posts.")
    return user


def _resolve_hub(session: Session, text: str, field: str) -> str:
    result = rs.resolve_hub(session, text)
    if result.hub is None:
        raise ApiError(result.code or "UNKNOWN_HUB", result.message or f"Unknown hub {text!r}.",
                       422 if result.code == "AMBIGUOUS_HUB" else 404, field,
                       {"candidates": result.candidates, "suggestions": result.suggestions})
    return result.hub.hub_id


def _find_edge(session: Session, mode: str, a: str, b: str) -> RouteEdge | None:
    for o, d in ((a, b), (b, a)):
        edge = session.exec(select(RouteEdge).where(RouteEdge.mode == mode, RouteEdge.origin_hub == o,
                                                    RouteEdge.destination_hub == d)
                            .order_by(RouteEdge.route_id)).first()
        if edge is not None:
            return edge
    return None


def estimate_total_fare(session: Session, mode: str, origin: str | None, destination: str | None,
                        scenario: str) -> tuple[FareRange | None, list[dict]]:
    """(total fare range or None, warnings). Real edge if one exists, else a transient unsaved edge."""
    if not origin or not destination:
        return None, [{"code": "FARE_ESTIMATE_UNAVAILABLE",
                       "message": "Origin and destination are both needed to estimate a fare."}]
    edge = _find_edge(session, mode, origin, destination)
    warnings: list[dict] = []
    if edge is None:
        # Never persisted: only used so fare_service can look up an explicit fare row or apply the formula.
        edge = RouteEdge(route_id="RIDESHARE_ESTIMATE", mode=mode, origin_hub=origin, destination_hub=destination)
        warnings.append({"code": "NO_ROUTE_EDGE",
                         "message": "No route edge exists for this mode between these hubs; "
                                    "the fare is an estimate from the fare data / formula."})
    try:
        leg = fs.calculate_leg_fare(session, edge, scenario)
    except ApiError as exc:  # FareError: distance unknown, formula disabled, rates missing...
        warnings.append({"code": "FARE_ESTIMATE_UNAVAILABLE", "message": exc.message})
        return None, warnings
    fare = fs.make_range(leg.regular_fare_min_bdt, leg.regular_fare_max_bdt, leg.fare_source)
    warnings.append({"code": "FARE_IS_ESTIMATE", "message": fs.ESTIMATE_NOTE})
    return fare, warnings


def _participants(session: Session, post_id: str) -> list[RideshareJoin]:
    return session.exec(select(RideshareJoin).where(RideshareJoin.post_id == post_id)
                        .order_by(RideshareJoin.joined_at, RideshareJoin.id)).all()


def _event(session: Session, kind: EventType, post: RidesharePost, user: User) -> None:
    event_service.publish_event(session, kind, {
        "post_id": post.post_id, "mode": post.mode, "origin_hub": post.origin_hub,
        "destination_hub": post.destination_hub, "hub_id": post.hub_id, "seats_total": post.seats_total,
        "seats_taken": post.seats_taken, "status": post.status, "user_id": str(user.id)})


def refresh_status(session: Session, post: RidesharePost) -> RidesharePost:
    """Lazy expiry: an open post past expires_at becomes `expired`."""
    if post.status in OPEN_STATUSES and post.expires_at and post.expires_at <= utcnow():
        post.status = RidesharePostStatus.expired.value
        session.add(post)
        session.commit()
        session.refresh(post)
    return post


def _load(session: Session, post_id: str) -> RidesharePost:
    post = session.get(RidesharePost, post_id)
    if post is None:
        raise api_error("RIDESHARE_NOT_FOUND", f"No ride-share post '{post_id}'.", details={"post_id": post_id})
    return refresh_status(session, post)


def _require_open(post: RidesharePost) -> None:
    if post.status not in OPEN_STATUSES:
        raise api_error("RIDESHARE_NOT_OPEN", f"This post is {post.status}.", details={"status": post.status})


# ----------------------------------------------------------------------------- fare split
def recalculate_fare_split(post: RidesharePost) -> RideShareSplit:
    """Equal split of the stored total over the current participants (fare_service.calculate_rideshare_split)."""
    lo, hi = post.estimated_total_fare_min_bdt, post.estimated_total_fare_max_bdt
    if lo is None or hi is None:
        return RideShareSplit(participants=post.seats_taken)
    r = fs.calculate_rideshare_split(fs.make_range(lo, hi, "formula"), post.seats_taken)
    return RideShareSplit(**r.model_dump())


# ----------------------------------------------------------------------------- commands
def create_post(session: Session, user: User | None, payload: RideShareCreate) -> RidesharePost:
    user = require_verified(user)
    mode = payload.mode.strip().lower()
    if mode not in values(TransitMode):
        raise api_error("VALIDATION_ERROR", f"Unknown mode {payload.mode!r}.", "mode",
                        {"allowed": [m for m in values(TransitMode) if m not in NOT_SHAREABLE_MODES]})
    if mode in NOT_SHAREABLE_MODES:
        raise api_error("RIDESHARE_MODE_NOT_SHAREABLE", f"{mode} cannot be shared as a ride.", "mode")
    if not (payload.origin_hub or payload.hub_id):
        raise api_error("RIDESHARE_LOCATION_REQUIRED", "Give origin_hub or hub_id.", "origin_hub")
    scenario = fs.normalize_scenario(payload.scenario)

    hub_id = _resolve_hub(session, payload.hub_id, "hub_id") if payload.hub_id else None
    origin = _resolve_hub(session, payload.origin_hub, "origin_hub") if payload.origin_hub else None
    destination = (_resolve_hub(session, payload.destination_hub, "destination_hub")
                   if payload.destination_hub else None)
    if origin and destination and origin == destination:
        raise api_error("SAME_ORIGIN_DESTINATION", "Origin and destination are the same hub.", "destination_hub")
    if hub_id is None and origin is not None:
        hub_id = origin  # the board's "where is this" hub

    fare, _ = estimate_total_fare(session, mode, origin or (hub_id if destination else None), destination, scenario)
    now = utcnow()
    post = RidesharePost(
        post_id=f"rs_{uuid.uuid4().hex[:12]}", origin_hub=origin, destination_hub=destination, hub_id=hub_id,
        mode=mode, created_by=user.id, seats_total=payload.seats_total, seats_taken=1,
        estimated_total_fare_min_bdt=fare.min_bdt if fare else None,
        estimated_total_fare_max_bdt=fare.max_bdt if fare else None,
        scenario=scenario, status=RidesharePostStatus.active.value, created_at=now,
        expires_at=now + timedelta(minutes=payload.expires_in_minutes), notes=payload.notes)
    session.add(post)
    session.flush()  # the join row references the post
    session.add(RideshareJoin(post_id=post.post_id, user_id=user.id, joined_at=now))
    session.flush()
    _event(session, EventType.rideshare_created, post, user)
    session.commit()
    session.refresh(post)
    return post


def join_post(session: Session, post_id: str, user: User | None) -> RidesharePost:
    user = require_verified(user)
    post = _load(session, post_id)
    if any(j.user_id == user.id for j in _participants(session, post_id)):
        return post  # already in: nothing changes, no event
    _require_open(post)
    if post.seats_taken >= post.seats_total:
        raise api_error("RIDESHARE_FULL", "No seats left on this post.", details={"seats_total": post.seats_total})
    session.add(RideshareJoin(post_id=post_id, user_id=user.id, joined_at=utcnow()))
    post.seats_taken += 1
    if post.seats_taken >= post.seats_total:
        post.status = RidesharePostStatus.full.value
    session.add(post)
    session.flush()
    _event(session, EventType.rideshare_joined, post, user)
    session.commit()
    session.refresh(post)
    return post


def leave_post(session: Session, post_id: str, user: User | None) -> RidesharePost:
    user = require_verified(user)
    post = _load(session, post_id)
    _require_open(post)
    mine = next((j for j in _participants(session, post_id) if j.user_id == user.id), None)
    if mine is None:
        raise api_error("NOT_A_PARTICIPANT", "You are not a participant of this post.")
    session.delete(mine)
    post.seats_taken = max(0, post.seats_taken - 1)
    emptied = post.seats_taken == 0
    if emptied:
        post.status = RidesharePostStatus.cancelled.value
    elif post.status == RidesharePostStatus.full.value and post.seats_taken < post.seats_total:
        post.status = RidesharePostStatus.active.value
    session.add(post)
    session.flush()
    _event(session, EventType.rideshare_left, post, user)
    if emptied:
        _event(session, EventType.rideshare_cancelled, post, user)
    session.commit()
    session.refresh(post)
    return post


def cancel_post(session: Session, post_id: str, user: User | None) -> RidesharePost:
    user = require_verified(user)
    post = _load(session, post_id)
    if post.created_by != user.id and user.role != UserRole.admin.value:
        raise api_error("FORBIDDEN", "Only the creator or an admin can cancel this post.")
    _require_open(post)
    post.status = RidesharePostStatus.cancelled.value
    session.add(post)
    session.flush()
    _event(session, EventType.rideshare_cancelled, post, user)
    session.commit()
    session.refresh(post)
    return post


# ----------------------------------------------------------------------------- queries
def list_posts(session: Session, filters: RideShareFilters | None = None) -> list[RidesharePost]:
    f = filters or RideShareFilters()
    # bring expiry up to date for posts that could still look open
    for stale in session.exec(select(RidesharePost).where(
            RidesharePost.status.in_(OPEN_STATUSES), RidesharePost.expires_at <= utcnow())).all():
        stale.status = RidesharePostStatus.expired.value
        session.add(stale)
    session.commit()

    q = select(RidesharePost)
    if f.hub_id:
        h = _resolve_hub(session, f.hub_id, "hub_id")
        q = q.where(or_(RidesharePost.hub_id == h, RidesharePost.origin_hub == h, RidesharePost.destination_hub == h))
    if f.origin_hub:
        q = q.where(RidesharePost.origin_hub == _resolve_hub(session, f.origin_hub, "origin_hub"))
    if f.destination_hub:
        q = q.where(RidesharePost.destination_hub == _resolve_hub(session, f.destination_hub, "destination_hub"))
    if f.mode:
        q = q.where(RidesharePost.mode == f.mode.strip().lower())
    if f.status:
        if f.status not in values(RidesharePostStatus):
            raise api_error("VALIDATION_ERROR", f"Unknown status {f.status!r}.", "status",
                            {"allowed": values(RidesharePostStatus)})
        q = q.where(RidesharePost.status == f.status)  # an explicit status overrides active_only
    elif f.active_only:
        q = q.where(RidesharePost.status.in_(OPEN_STATUSES))
    return list(session.exec(q.order_by(RidesharePost.created_at.desc(), RidesharePost.post_id)).all())


def post_detail(session: Session, post: RidesharePost, current_user: User | None = None) -> dict:
    """The API view of a post (see docs/RIDESHARE.md). Contains no payment fields."""
    joins = _participants(session, post.post_id)
    users = {u.id: u for u in session.exec(select(User).where(User.id.in_([j.user_id for j in joins] + [post.created_by]))).all()}
    creator = users.get(post.created_by)
    split = recalculate_fare_split(post).model_dump(mode="json")  # rounded to 2 decimals
    warnings: list[dict] = []
    if post.estimated_total_fare_min_bdt is None:
        warnings.append({"code": "FARE_ESTIMATE_UNAVAILABLE",
                         "message": "No fare estimate is available for this post."})
    else:
        warnings.append({"code": "FARE_IS_ESTIMATE", "message": fs.ESTIMATE_NOTE})
        if post.origin_hub and post.destination_hub and _find_edge(session, post.mode, post.origin_hub,
                                                                  post.destination_hub) is None:
            warnings.append({"code": "NO_ROUTE_EDGE",
                             "message": "No route edge exists for this mode between these hubs; "
                                        "the fare is an estimate from the fare data / formula."})
    return {
        "post_id": post.post_id, "mode": post.mode, "origin_hub": post.origin_hub,
        "destination_hub": post.destination_hub, "hub_id": post.hub_id,
        "created_by": {"user_id": str(post.created_by), "name": creator.name if creator else ""},
        "seats_total": post.seats_total, "seats_taken": post.seats_taken,
        "seats_remaining": max(0, post.seats_total - post.seats_taken), "status": post.status,
        "estimated_total_fare_min_bdt": split["total_fare_min_bdt"],
        "estimated_total_fare_max_bdt": split["total_fare_max_bdt"],
        "per_person_min_bdt": split["per_person_min_bdt"], "per_person_max_bdt": split["per_person_max_bdt"],
        "suggested_safe_per_person_bdt": split["suggested_safe_per_person_bdt"], "fare_split": split,
        "fare_disclaimer": UPPER_BOUND_LABEL, "note": COORDINATION_NOTE, "notes": post.notes,
        "scenario": post.scenario or "normal", "created_at": _iso(post.created_at), "expires_at": _iso(post.expires_at),
        "participants": [{"user_id": str(j.user_id), "name": users[j.user_id].name if j.user_id in users else "",
                          "joined_at": _iso(j.joined_at)} for j in joins],
        "my_participation": bool(current_user and any(j.user_id == current_user.id for j in joins)),
        "warnings": warnings,
    }


def get_post(session: Session, post_id: str, current_user: User | None = None) -> dict:
    return post_detail(session, _load(session, post_id), current_user)
