"""Ride-share board endpoints (see docs/RIDESHARE.md). Guests browse; verified users create/join/leave/cancel."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.deps import get_current_verified_user, get_db, get_optional_user
from app.models import User
from app.responses import success
from app.schemas import RideShareCreate, RideShareFilters
from app.services import rideshare_service as svc

router = APIRouter()


@router.get("/api/rideshare/posts")
def list_posts(hub_id: Optional[str] = None, origin_hub: Optional[str] = None,
               destination_hub: Optional[str] = None, mode: Optional[str] = None, status: Optional[str] = None,
               active_only: bool = Query(True), user: User | None = Depends(get_optional_user),
               db: Session = Depends(get_db)):
    filters = RideShareFilters(hub_id=hub_id, origin_hub=origin_hub, destination_hub=destination_hub, mode=mode,
                               status=status, active_only=active_only)
    posts = svc.list_posts(db, filters)
    return success({"posts": [svc.post_detail(db, p, user) for p in posts], "count": len(posts)})


@router.post("/api/rideshare/posts")
def create_post(body: RideShareCreate, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    return success(svc.post_detail(db, svc.create_post(db, user, body), user))


@router.post("/api/rideshare/posts/{post_id}/join")
def join_post(post_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    return success(svc.post_detail(db, svc.join_post(db, post_id, user), user))


@router.post("/api/rideshare/posts/{post_id}/leave")
def leave_post(post_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    return success(svc.post_detail(db, svc.leave_post(db, post_id, user), user))


@router.post("/api/rideshare/posts/{post_id}/cancel")
def cancel_post(post_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    return success(svc.post_detail(db, svc.cancel_post(db, post_id, user), user))
