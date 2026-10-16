"""Admin CMS endpoints: road block/unblock, fare-rule editing and user verification.

Route search rebuilds its graph from the database on every request, so a block/unblock or fare-rule change takes
effect on the next search with no cache to clear.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.deps import get_current_admin, get_db
from app.enums import EventType, RouteEdgeStatus, TransitMode, UserStatus
from app.models import FareRule, RouteEdge, User, utcnow
from app.responses import api_error, success
from app.schemas import FareRuleOut, UserPublic
from app.services import event_service

router = APIRouter()


class RouteBlockRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class FareRuleUpdate(BaseModel):
    student_discount_percent: float = Field(ge=0, le=100)
    minimum_student_fare_bdt: Optional[float] = Field(default=None, ge=0)
    surge_allowed: bool = False
    use_official_matrix: bool = False
    allow_formula_fallback: bool = True


def _edge_out(edge: RouteEdge) -> dict:
    return {"route_id": edge.route_id, "mode": edge.mode, "origin_hub": edge.origin_hub,
            "destination_hub": edge.destination_hub, "status": edge.status, "closed_reason": edge.closed_reason}


def _get_edge(db: Session, route_id: str) -> RouteEdge:
    edge = db.get(RouteEdge, route_id)
    if edge is None:
        raise api_error("ROUTE_NOT_FOUND", f"No route edge {route_id!r}.", "route_id")
    return edge


def _set_edge_status(db: Session, admin: User, route_id: str, status: str, reason: Optional[str]) -> dict:
    edge = _get_edge(db, route_id)
    edge.status = status
    edge.closed_reason = reason if status == RouteEdgeStatus.closed.value else None
    edge.updated_at = utcnow()
    db.add(edge)
    event_service.publish_event(db, EventType.route_recalculated, {
        "action": "blocked" if status == RouteEdgeStatus.closed.value else "unblocked",
        "route_id": route_id, "reason": edge.closed_reason, "by_admin": admin.id,
    }, route_id=route_id)
    db.commit()
    db.refresh(edge)
    return _edge_out(edge)


@router.post("/api/admin/routes/{route_id}/block")
def block_route(route_id: str, body: RouteBlockRequest | None = None, db: Session = Depends(get_db),
                admin: User = Depends(get_current_admin)):
    reason = (body.reason if body else None) or "Blocked by admin"
    return success(_set_edge_status(db, admin, route_id, RouteEdgeStatus.closed.value, reason))


@router.post("/api/admin/routes/{route_id}/unblock")
def unblock_route(route_id: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return success(_set_edge_status(db, admin, route_id, RouteEdgeStatus.active.value, None))


@router.get("/api/admin/fare-rules")
def list_fare_rules(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    rules = db.exec(select(FareRule).order_by(FareRule.mode)).all()
    return success([FareRuleOut.model_validate(r).model_dump(mode="json") for r in rules])


@router.put("/api/admin/fare-rules/{mode}")
def replace_fare_rule(mode: str, body: FareRuleUpdate, db: Session = Depends(get_db),
                      admin: User = Depends(get_current_admin)):
    if mode not in {m.value for m in TransitMode}:
        raise api_error("VALIDATION_ERROR", f"Unknown mode {mode!r}.", "mode",
                        {"allowed": [m.value for m in TransitMode]})
    rule = db.get(FareRule, mode) or FareRule(mode=mode)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    rule.updated_by = admin.id
    rule.updated_at = utcnow()
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return success(FareRuleOut.model_validate(rule).model_dump(mode="json"))


@router.get("/api/admin/users")
def list_users(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    users = db.exec(select(User).order_by(User.id)).all()
    return success([UserPublic.model_validate(u).model_dump(mode="json") for u in users])


def _set_user(db: Session, user_id: int, **fields) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise api_error("USER_NOT_FOUND", f"No user {user_id}.", "user_id")
    for k, v in fields.items():
        setattr(user, k, v)
    user.updated_at = utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserPublic.model_validate(user).model_dump(mode="json")


@router.post("/api/admin/users/{user_id}/verify")
def verify_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return success(_set_user(db, user_id, is_verified=True))


@router.post("/api/admin/users/{user_id}/disable")
def disable_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    if user_id == admin.id:
        raise api_error("FORBIDDEN", "You cannot disable your own account.")
    return success(_set_user(db, user_id, status=UserStatus.disabled.value))


@router.post("/api/admin/users/{user_id}/enable")
def enable_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return success(_set_user(db, user_id, status=UserStatus.active.value))
