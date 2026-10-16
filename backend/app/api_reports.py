"""Report, vote, moderation and event endpoints (see docs/REPORTS_API.md)."""
import hmac
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlmodel import Session, select

from app.config import settings
from app.deps import get_current_admin, get_current_verified_user, get_db, get_optional_user
from app.enums import EventType, ReportStatus, ReportType
from app.models import Report, ReportImage, User
from app.responses import api_error, success
from app.schemas import (
    AdminRejectRequest, AdminReopenRequest, AdminVerifyRequest, DebugEventPublish, DraftOut, EventOut,
    ReportCreate, ReportImageOut, ReportOut, VoteRequest,
)
from app.services import alert_service, event_service
from app.services import routing_service as rs

router = APIRouter()

FORM_LIST_FIELDS = ("affected_route_ids",)
FORM_FIELDS = ("report_type", "severity", "affected_hub", "description", "mode", "fare_amount_bdt", "incident_time",
               "session_id")


# ============================================================================= views
def _image_url(file_path: str) -> str:
    try:
        rel = (Path(alert_service.BASE_DIR) / file_path).resolve().relative_to(alert_service.upload_root() / "reports")
        return "/uploads/reports/" + rel.as_posix()
    except ValueError:
        return file_path


def report_view(db: Session, report: Report, user: User | None) -> dict:
    images = db.exec(select(ReportImage).where(ReportImage.report_id == report.report_id).order_by(ReportImage.id)).all()
    view = ReportOut.model_validate(report).model_copy(update={
        "my_vote": alert_service.get_user_vote(db, report.report_id, user.id) if user else None,
        "images": [ReportImageOut(id=i.id, url=_image_url(i.file_path), mime=i.mime, size_bytes=i.size_bytes) for i in images],
        "flags": list((report.raw_json or {}).get("sanity_flags") or []),
    })
    return view.model_dump(mode="json")


def _fresh(db: Session, report: Report) -> Report:
    """Bring decay/expiry up to date before showing a report."""
    alert_service.refresh_decay(db, report)
    db.commit()
    db.refresh(report)
    return report


# ============================================================================= create
def _payload_from_form(form) -> dict:
    if form.get("payload"):
        try:
            data = json.loads(form["payload"])
        except ValueError:
            raise api_error("VALIDATION_ERROR", "`payload` is not valid JSON.", "payload")
        if not isinstance(data, dict):
            raise api_error("VALIDATION_ERROR", "`payload` must be a JSON object.", "payload")
        return data
    data = {k: form.get(k) for k in FORM_FIELDS if form.get(k) not in (None, "")}
    routes: list[str] = []
    for raw in form.getlist("affected_route_ids"):
        raw = str(raw).strip()
        if raw.startswith("["):
            try:
                routes += [str(x) for x in json.loads(raw)]
                continue
            except ValueError:
                pass
        routes += [x.strip() for x in raw.split(",") if x.strip()]
    if routes:
        data["affected_route_ids"] = routes
    return data


def _validated(payload: dict) -> ReportCreate:
    try:
        return ReportCreate.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in exc.errors()])


@router.post("/api/reports")
async def create_report(request: Request, user: User = Depends(get_current_verified_user),
                        db: Session = Depends(get_db)):
    """JSON body, or multipart/form-data (fields or a `payload` JSON string, plus up to 5 `images`).
    The report starts `pending`; it never auto-verifies."""
    images: list[alert_service.ImageUpload] = []
    if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        form = await request.form()
        payload = _payload_from_form(form)
        for f in form.getlist("images"):
            if getattr(f, "filename", None):
                data = await f.read(alert_service.MAX_IMAGE_BYTES + 1)
                images.append(alert_service.ImageUpload(f.filename, (f.content_type or "").lower(), data))
    else:
        try:
            payload = await request.json()
        except ValueError:
            raise api_error("VALIDATION_ERROR", "Request body must be valid JSON.")
        if not isinstance(payload, dict):
            raise api_error("VALIDATION_ERROR", "Request body must be a JSON object.")
    report = alert_service.create_report(db, user, _validated(payload), images=images)
    sanity = (report.raw_json or {}).get("sanity", {})
    return success({"report": report_view(db, report, user),
                    "sanity": {"flags": sanity.get("flags", []), "notes": sanity.get("notes", []),
                               "needs_admin_review": report.needs_admin_review}}, status_code=201)


@router.post("/api/reports/drafts/{draft_id}/submit")
def submit_draft(draft_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    report = alert_service.submit_draft(db, draft_id, user)
    draft = db.get(alert_service.ReportDraft, draft_id)
    return success({"report": report_view(db, report, user), "draft": DraftOut.model_validate(draft).model_dump(mode="json")},
                   status_code=201)


@router.post("/api/reports/drafts/{draft_id}/cancel")
def cancel_draft(draft_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    draft = alert_service.cancel_draft(db, draft_id, user)
    return success({"draft": DraftOut.model_validate(draft).model_dump(mode="json")})


# ============================================================================= read
@router.get("/api/reports")
def list_reports(status: Optional[ReportStatus] = None, report_type: Optional[ReportType] = None,
                 affected_hub: Optional[str] = Query(None, max_length=120), since: Optional[datetime] = None,
                 limit: int = Query(50, ge=1, le=200), user: User | None = Depends(get_optional_user),
                 db: Session = Depends(get_db)):
    """Public. Newest first. Decay and expiry are brought up to date on every read."""
    alert_service.expire_old_reports(db)
    query = select(Report)
    if status:
        query = query.where(Report.status == status.value)
    if report_type:
        query = query.where(Report.report_type == report_type.value)
    if affected_hub:
        hub = rs.resolve_hub(db, affected_hub).hub
        query = query.where(Report.affected_hub == (hub.hub_id if hub else affected_hub))
    if since:
        query = query.where(Report.created_at >= alert_service._naive_utc(since))
    reports = db.exec(query.order_by(Report.created_at.desc(), Report.report_id).limit(limit)).all()
    for r in reports:
        alert_service.refresh_decay(db, r)
    db.commit()
    return success([report_view(db, r, user) for r in reports])


@router.get("/api/reports/{report_id}")
def get_report(report_id: str, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    report = alert_service._get_report(db, report_id)
    return success(report_view(db, _fresh(db, report), user))


# ============================================================================= votes & lifecycle
@router.post("/api/reports/{report_id}/vote")
def vote(report_id: str, body: VoteRequest, user: User = Depends(get_current_verified_user),
         db: Session = Depends(get_db)):
    report = alert_service.vote_report(db, report_id, user, body.value)
    return success(report_view(db, report, user))


@router.delete("/api/reports/{report_id}/vote")
def retract_vote(report_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    report = alert_service.retract_vote(db, report_id, user)
    return success(report_view(db, report, user))


@router.post("/api/reports/{report_id}/confirm")
def confirm(report_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    report = alert_service.confirm_report(db, report_id, user)
    return success(report_view(db, report, user))


@router.post("/api/reports/{report_id}/resolve")
def resolve(report_id: str, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    report = alert_service.resolve_report(db, report_id, user)
    return success(report_view(db, report, user))


# ============================================================================= admin
@router.post("/api/admin/reports/{report_id}/verify")
def admin_verify(report_id: str, body: Optional[AdminVerifyRequest] = None, admin: User = Depends(get_current_admin),
                 db: Session = Depends(get_db)):
    body = body or AdminVerifyRequest()
    report = alert_service.admin_verify(db, report_id, admin, body.note, edge_closed=body.edge_closed,
                                        extra_time_min=body.extra_time_min)
    sanity = (report.raw_json or {}).get("sanity_at_verification", {})
    return success({"report": report_view(db, report, admin),
                    "sanity": {"flags": sanity.get("flags", []), "notes": sanity.get("notes", [])}})


@router.post("/api/admin/reports/{report_id}/reject")
def admin_reject(report_id: str, body: AdminRejectRequest, admin: User = Depends(get_current_admin),
                 db: Session = Depends(get_db)):
    return success(report_view(db, alert_service.admin_reject(db, report_id, admin, body.reason), admin))


@router.post("/api/admin/reports/{report_id}/reopen")
def admin_reopen(report_id: str, body: Optional[AdminReopenRequest] = None, admin: User = Depends(get_current_admin),
                 db: Session = Depends(get_db)):
    body = body or AdminReopenRequest()
    return success(report_view(db, alert_service.admin_reopen(db, report_id, admin, body.reason), admin))


# ============================================================================= events
@router.get("/api/events")
def list_events(since_id: int = Query(0, ge=0), session_id: Optional[str] = Query(None, max_length=100),
                limit: int = Query(100, ge=1, le=event_service.MAX_LIMIT), event_type: Optional[EventType] = None,
                db: Session = Depends(get_db)):
    """Poll with the returned `next_since_id`. Global events, plus `session_id`'s own events if given."""
    events = event_service.get_events(db, since_id, session_id, limit, event_type.value if event_type else None)
    next_id = events[-1].event_id if events else since_id
    more = bool(events) and bool(event_service.get_events(db, next_id, session_id, 1, event_type.value if event_type else None))
    return success({"events": [EventOut.model_validate(e).model_dump(mode="json") for e in events],
                    "next_since_id": next_id, "has_more": more})


@router.post("/api/events/publish-debug")
def publish_debug(body: DebugEventPublish, x_demo_secret: Optional[str] = Header(None, alias="X-Demo-Secret"),
                  db: Session = Depends(get_db)):
    """Demo helper: inserts one event. Protected by DEMO_SECRET (header X-Demo-Secret or body `secret`).
    It writes to the event log only; it cannot close routes or change any report."""
    supplied = x_demo_secret or body.secret or ""
    if not hmac.compare_digest(supplied.encode(), settings.demo_secret.encode()):
        raise api_error("DEBUG_SECRET_INVALID", "Missing or wrong demo secret.")
    event = event_service.publish_event(db, body.event_type, {**body.payload, "debug": True}, body.session_id,
                                        body.report_id, body.route_id)
    db.commit()
    return success({"event": EventOut.model_validate(event).model_dump(mode="json")}, status_code=201)
