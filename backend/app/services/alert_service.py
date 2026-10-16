"""Reports (alerts): drafts, sanity checks, votes, admin moderation, decay/expiry, events.

Rules enforced here (see docs/REPORTS_API.md):
  * only verified, active users create / vote / confirm / resolve; admins moderate;
  * a chat/frontend draft becomes a `pending` report only when the user submits it;
  * votes move confidence / freshness / community_score / needs_admin_review, never the status;
  * only an admin verifies, rejects or reopens; nothing is ever auto-verified;
  * only verified, confident, unexpired reports with a hub or routes affect routing.

Every function takes the SQLModel `session` first and commits its own transaction (events included).
Times are naive UTC.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, func, select

from app.config import BASE_DIR, settings
from app.enums import (
    DraftStatus, EventType, ReportStatus, ReportType, TransitMode, UserRole, UserStatus,
)
from app.models import Hub, Report, ReportDraft, ReportImage, ReportVote, RouteEdge, User, utcnow
from app.responses import api_error
from app.schemas import FareSanityResult, ReportCreate
from app.services import alert_rules as rules
from app.services import event_service
from app.services import fare_service as fs
from app.services.fare_service import FareError

INCIDENT_MAX_AGE = timedelta(hours=24)
FUTURE_SKEW = timedelta(seconds=60)
DUPLICATE_WINDOW = timedelta(minutes=10)
CONTRADICTION_WINDOW = timedelta(minutes=60)
DRAFT_TTL = timedelta(hours=24)
COMMUNITY_REVIEW_THRESHOLD = 3  # |upvotes - downvotes| that asks an admin to look
HAZARD_TYPES = {ReportType.waterlogging.value, ReportType.intersection_blockade.value, ReportType.severe_gridlock.value}

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


# ============================================================================= helpers
def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _require_active_verified(user: User) -> None:
    if user.status != UserStatus.active.value:
        raise api_error("ACCOUNT_DISABLED", "This account is disabled.")
    if not user.is_verified:
        raise api_error("NOT_VERIFIED", "Only verified users can do this.")


def _require_admin(user: User) -> None:
    _require_active_verified(user)
    if user.role != UserRole.admin.value:
        raise api_error("FORBIDDEN", "Admin access required.")


def _get_report(session: Session, report_id: str) -> Report:
    report = session.get(Report, report_id)
    if report is None:
        raise api_error("REPORT_NOT_FOUND", f"No report '{report_id}'.", details={"report_id": report_id})
    return report


def _as_payload(payload: ReportCreate | dict) -> ReportCreate:
    if isinstance(payload, ReportCreate):
        return payload
    return ReportCreate.model_validate(payload)


def _raw(report: Report) -> dict:
    return dict(report.raw_json or {})


def _transition_error(report: Report, allowed: tuple[str, ...], action: str):
    return api_error("INVALID_STATUS_TRANSITION",
                     f"Cannot {action} a report that is '{report.status}' (allowed from: {', '.join(allowed)}).",
                     details={"status": report.status, "allowed_from": list(allowed)})


def report_event_payload(report: Report, **extra) -> dict:
    """Public, non-personal summary used in event payloads."""
    return {"report_id": report.report_id, "status": report.status, "report_type": report.report_type,
            "severity": report.severity, "affected_hub": report.affected_hub,
            "affected_route_ids": list(report.affected_route_ids_json or []), "confidence": round(report.confidence, 4),
            "affects_routing": report.affects_routing, **extra}


def publish_event(session: Session, event_type: str | EventType, payload: dict | None = None,
                  session_id: str | None = None, report_id: str | None = None, route_id: str | None = None):
    return event_service.publish_event(session, event_type, payload, session_id, report_id, route_id)


def get_events(session: Session, since_id: int = 0, session_id: str | None = None, limit: int = 100):
    return event_service.get_events(session, since_id, session_id, limit)


def _needs_review(report: Report) -> bool:
    if report.status not in rules.LIVE_STATUSES:
        return False
    if (_raw(report).get("sanity_flags") or []):
        return True
    score = report.upvotes - report.downvotes
    return score <= -COMMUNITY_REVIEW_THRESHOLD or (report.status == ReportStatus.pending.value
                                                    and score >= COMMUNITY_REVIEW_THRESHOLD)


def _recalculate(report: Report, now: datetime) -> Report:
    rules.apply_decay(report, now)
    report.needs_admin_review = _needs_review(report)
    return report


def _recount_votes(session: Session, report: Report) -> None:
    rows = session.exec(select(ReportVote.value, func.count()).where(ReportVote.report_id == report.report_id)
                        .group_by(ReportVote.value)).all()
    counts = {value: n for value, n in rows}
    report.upvotes, report.downvotes = counts.get(1, 0), counts.get(-1, 0)


def _publish_recalc(session: Session, report: Report, reason: str) -> None:
    routes = list(report.affected_route_ids_json or [])
    publish_event(session, EventType.route_recalculated,
                  {"reason": reason, "report_id": report.report_id, "affected_hub": report.affected_hub,
                   "affected_route_ids": routes},
                  report_id=report.report_id, route_id=routes[0] if len(routes) == 1 else None)


# ============================================================================= decay & expiry
def _expire(session: Session, report: Report, now: datetime) -> None:
    was_routing = report.affects_routing
    report.status = ReportStatus.expired.value
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_expired, report_event_payload(report), report_id=report.report_id)
    if was_routing:
        _publish_recalc(session, report, "report_expired")


def refresh_decay(session: Session, report: Report, now: datetime | None = None) -> Report:
    """Bring confidence/freshness/affects_routing up to date, expiring the report if its time is up.
    Flushes but does not commit (callers / read endpoints commit)."""
    now = now or utcnow()
    if report.status in rules.LIVE_STATUSES and now >= report.expires_at:
        _expire(session, report, now)
    else:
        _recalculate(report, now)
        session.add(report)
    session.flush()
    return report


def expire_old_reports(session: Session, now: datetime | None = None) -> list[Report]:
    now = now or utcnow()
    due = session.exec(select(Report).where(Report.status.in_(rules.LIVE_STATUSES), Report.expires_at <= now)).all()
    for report in due:
        _expire(session, report, now)
    session.commit()
    return list(due)


def get_routing_impacting_alerts(session: Session, now: datetime | None = None) -> list[Report]:
    """Reports that affect routing right now (verified, confidence >= 0.40, unexpired, with a hub/routes)."""
    now = now or utcnow()
    candidates = session.exec(select(Report).where(
        Report.status == ReportStatus.verified.value, Report.expires_at > now).order_by(Report.report_id)).all()
    return [r for r in candidates if rules.affects_routing(r, now)]


# ============================================================================= sanity checks
class SanityResult(BaseModel):
    ok: bool = True  # no blocking errors
    errors: list[dict] = Field(default_factory=list)  # blocking: {code, message, field}
    flags: list[str] = Field(default_factory=list)  # non-blocking: duplicate, contradiction, unrealistic_fare
    needs_admin_review: bool = False
    contradiction_flag: bool = False
    resolved_hub_id: Optional[str] = None
    incident_time: Optional[datetime] = None
    duplicate_of: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    fare_check: Optional[FareSanityResult] = None
    notes: list[str] = Field(default_factory=list)


def _err(result: SanityResult, code: str, message: str, field: str | None = None, **details) -> None:
    result.errors.append({"code": code, "message": message, "field": field, "details": details})
    result.ok = False


def _reference(r: Report) -> datetime:
    return r.last_verified_at or r.created_at


def run_sanity_checks(session: Session, payload: ReportCreate | dict, user: User | None, now: datetime | None = None,
                      *, stage: str = "submit", exclude_report_id: str | None = None) -> SanityResult:
    """Validate a report payload.

    Blocking errors: future/too-old incident time, unknown hub or routes, fare_surge without mode/amount.
    Flags (report stays pending, needs_admin_review): duplicate, contradiction, unrealistic fare.
    stage="verify" skips the incident-age and duplicate checks (the admin is reviewing an existing report).
    """
    from app.services import routing_service as rs  # local import: routing imports alert_rules, not this module

    now = now or utcnow()
    p = _as_payload(payload)
    result = SanityResult()

    # --- incident time
    incident = _naive_utc(p.incident_time) or now
    result.incident_time = incident
    if stage == "submit":
        if incident > now + FUTURE_SKEW:
            _err(result, "INCIDENT_TIME_FUTURE", "The incident time is in the future.", "incident_time")
        elif incident < now - INCIDENT_MAX_AGE:
            _err(result, "INCIDENT_TIME_TOO_OLD", "The incident is more than 24 hours old.", "incident_time")

    # --- hub & routes must exist
    if p.affected_hub:
        resolved = rs.resolve_hub(session, p.affected_hub)
        if resolved.hub is None or resolved.matched_by not in ("hub_id", "name", "alias"):
            _err(result, "UNKNOWN_HUB", f"Unknown hub '{p.affected_hub}'.", "affected_hub",
                 suggestions=resolved.suggestions or resolved.candidates)
        else:
            result.resolved_hub_id = resolved.hub.hub_id
    route_ids = list(dict.fromkeys(p.affected_route_ids))
    edges = {rid: session.get(RouteEdge, rid) for rid in route_ids}
    missing = [rid for rid, e in edges.items() if e is None]
    if missing:
        _err(result, "ROUTE_NOT_FOUND", f"Unknown route id(s): {', '.join(missing)}.", "affected_route_ids",
             missing=missing)

    # --- fare_surge needs mode + amount; any mode+amount gets a plausibility check
    if p.report_type == ReportType.fare_surge.value and (not p.mode or p.fare_amount_bdt is None):
        _err(result, "FARE_SURGE_FIELDS_REQUIRED", "A fare_surge report must include `mode` and `fare_amount_bdt`.",
             "mode" if not p.mode else "fare_amount_bdt")
    if p.mode and p.fare_amount_bdt is not None and not result.errors:
        found = [e for e in edges.values() if e is not None]
        edge = found[0] if found else None
        distance = None
        if edge is not None:
            distance = edge.road_distance_km
            if distance is None:
                try:
                    distance = fs.calculate_distance_km(edge, session.get(Hub, edge.origin_hub),
                                                        session.get(Hub, edge.destination_hub))
                except FareError:
                    distance = None
        check = fs.validate_submitted_fare(
            session, p.mode.value if isinstance(p.mode, TransitMode) else p.mode, p.fare_amount_bdt, distance,
            "normal", edge.origin_hub if edge else None, edge.destination_hub if edge else None)
        result.fare_check = check
        if check.needs_admin_review:
            result.flags.append("unrealistic_fare")
            result.notes += check.reasons
        elif not check.checked:
            result.notes += check.reasons

    if result.errors:
        return result

    # --- duplicate: same user + same hub (or same routes when no hub) + same type within 10 minutes
    if stage == "submit" and user is not None:
        recent = session.exec(select(Report).where(
            Report.reported_by == user.id, Report.report_type == p.report_type,
            Report.created_at >= now - DUPLICATE_WINDOW)).all()
        hub = result.resolved_hub_id
        for r in recent:
            same_place = (hub is not None and r.affected_hub == hub) or (
                hub is None and r.affected_hub is None and set(route_ids) & set(r.affected_route_ids_json or []))
            if same_place and r.report_id != exclude_report_id:
                result.duplicate_of.append(r.report_id)
        if result.duplicate_of:
            result.flags.append("duplicate")
            result.notes.append(f"Same user reported the same {p.report_type} here within 10 minutes: "
                                f"{', '.join(result.duplicate_of)}.")

    # --- contradiction against ACTIVE VERIFIED reports at the same hub / on the same routes
    others = session.exec(select(Report).where(
        Report.status == ReportStatus.verified.value, Report.expires_at > now)).all()
    hub = result.resolved_hub_id
    for o in others:
        if o.report_id == exclude_report_id:
            continue
        same_hub = hub is not None and o.affected_hub == hub
        shared_routes = bool(set(route_ids) & set(o.affected_route_ids_json or []))
        if not (same_hub or shared_routes):
            continue
        fresh = _reference(o) >= now - CONTRADICTION_WINDOW
        clash = False
        if p.report_type == ReportType.all_clear.value:
            clash = (same_hub and fresh and o.report_type in HAZARD_TYPES) or (shared_routes and o.edge_closed)
        elif p.report_type in HAZARD_TYPES:
            clash = o.report_type == ReportType.all_clear.value and ((same_hub and fresh) or shared_routes)
        if clash:
            result.conflicts_with.append(o.report_id)
    if result.conflicts_with:
        result.flags.append("contradiction")
        result.contradiction_flag = True
        result.notes.append(f"Contradicts active verified report(s): {', '.join(result.conflicts_with)}.")

    result.needs_admin_review = bool(result.flags)
    return result


def _raise_first(result: SanityResult):
    first = result.errors[0]
    raise api_error(first["code"], first["message"], first.get("field"),
                    {"errors": result.errors, **(first.get("details") or {})})


# ============================================================================= images
@dataclass
class ImageUpload:
    filename: str
    content_type: str
    data: bytes


def _looks_like(content_type: str, data: bytes) -> bool:
    if content_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if content_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if content_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def validate_images(images: list[ImageUpload]) -> None:
    if len(images) > settings.max_upload_images:
        raise api_error("IMAGE_TOO_MANY", f"At most {settings.max_upload_images} images per report.", "images")
    for img in images:
        if img.content_type not in ALLOWED_IMAGE_TYPES:
            raise api_error("IMAGE_TYPE_NOT_ALLOWED",
                            f"'{img.filename}': only JPEG, PNG and WebP images are accepted.", "images",
                            {"allowed": list(ALLOWED_IMAGE_TYPES)})
        if len(img.data) > MAX_IMAGE_BYTES:
            raise api_error("IMAGE_TOO_LARGE", f"'{img.filename}' is larger than 5 MB.", "images")
        if not img.data or not _looks_like(img.content_type, img.data):
            raise api_error("IMAGE_INVALID", f"'{img.filename}' is not a valid {img.content_type} file.", "images")


def upload_root() -> Path:
    root = Path(settings.upload_dir)
    return root if root.is_absolute() else (BASE_DIR / root).resolve()


def _save_images(session: Session, report: Report, images: list[ImageUpload]) -> list[Path]:
    folder = upload_root() / "reports" / report.report_id
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for img in images:
        path = folder / f"{uuid.uuid4().hex[:12]}{ALLOWED_IMAGE_TYPES[img.content_type]}"
        path.write_bytes(img.data)
        written.append(path)
        try:
            stored = path.relative_to(BASE_DIR).as_posix()
        except ValueError:
            stored = path.as_posix()
        session.add(ReportImage(report_id=report.report_id, file_path=stored, mime=img.content_type,
                                size_bytes=len(img.data)))
    return written


# ============================================================================= drafts
def create_draft(session: Session, session_id: str, user_id: int | None, payload: ReportCreate | dict) -> ReportDraft:
    """Store a report draft (e.g. from chat). Nothing is validated against the network yet and no report exists."""
    p = _as_payload(payload)
    draft = ReportDraft(draft_id=f"drf_{uuid.uuid4().hex[:12]}", session_id=session_id, user_id=user_id,
                        payload_json=p.model_dump(mode="json"), status=DraftStatus.draft.value)
    session.add(draft)
    session.flush()
    publish_event(session, EventType.report_draft_created, {"draft_id": draft.draft_id}, session_id=session_id)
    session.commit()
    return draft


def _get_draft(session: Session, draft_id: str, user: User) -> ReportDraft:
    draft = session.get(ReportDraft, draft_id)
    if draft is None:
        raise api_error("DRAFT_NOT_FOUND", f"No draft '{draft_id}'.", details={"draft_id": draft_id})
    if draft.user_id is not None and draft.user_id != user.id and user.role != UserRole.admin.value:
        raise api_error("DRAFT_FORBIDDEN", "This draft belongs to another user.")
    return draft


def submit_draft(session: Session, draft_id: str, user: User) -> Report:
    """draft -> pending report. Validates the stored payload now; on failure the draft stays open."""
    _require_active_verified(user)
    draft = _get_draft(session, draft_id, user)
    if draft.status != DraftStatus.draft.value:
        raise api_error("DRAFT_NOT_OPEN", f"Draft is already {draft.status}.", details={"status": draft.status})
    now = utcnow()
    if now - draft.created_at > DRAFT_TTL:
        draft.status = DraftStatus.expired.value
        session.add(draft)
        session.commit()
        raise api_error("DRAFT_EXPIRED", "This draft is older than 24 hours; start a new report.")
    try:
        payload = ReportCreate.model_validate(draft.payload_json)
        publish_event(session, EventType.report_draft_submitted, {"draft_id": draft.draft_id},
                      session_id=draft.session_id)
        report = _create_report(session, user, payload, session_id=draft.session_id, draft_id=draft.draft_id, now=now)
        draft.status = DraftStatus.submitted.value
        draft.submitted_at = now
        draft.user_id = user.id
        session.add(draft)
        session.commit()
    except ValidationError as exc:
        session.rollback()
        raise api_error("DRAFT_INVALID", f"The draft payload is invalid: {exc.errors()[0]['msg']}",
                        details={"errors": [e["msg"] for e in exc.errors()]})
    except Exception:
        session.rollback()
        raise
    session.refresh(report)
    return report


def cancel_draft(session: Session, draft_id: str, user: User) -> ReportDraft:
    _require_active_verified(user)
    draft = _get_draft(session, draft_id, user)
    if draft.status != DraftStatus.draft.value:
        raise api_error("DRAFT_NOT_OPEN", f"Draft is already {draft.status}.", details={"status": draft.status})
    draft.status = DraftStatus.cancelled.value  # no cancel event type exists in the contract
    session.add(draft)
    session.commit()
    return draft


# ============================================================================= create
def _create_report(session: Session, user: User, payload: ReportCreate | dict, *, images: list[ImageUpload] | None = None,
                   session_id: str | None = None, draft_id: str | None = None, now: datetime | None = None) -> Report:
    _require_active_verified(user)
    p = _as_payload(payload)
    now = now or utcnow()
    images = images or []
    validate_images(images)
    sanity = run_sanity_checks(session, p, user, now)
    if sanity.errors:
        _raise_first(sanity)

    half_life = rules.half_life_for(p.report_type)
    report = Report(
        report_id=f"rpt_{uuid.uuid4().hex[:12]}", report_type=p.report_type, severity=p.severity,
        affected_hub=sanity.resolved_hub_id, affected_route_ids_json=list(dict.fromkeys(p.affected_route_ids)),
        description=p.description.strip(), mode=p.mode.value if p.mode else None, fare_amount_bdt=p.fare_amount_bdt,
        reported_by=user.id, incident_time=sanity.incident_time, created_at=now, last_verified_at=None,
        half_life_min=half_life, expires_at=now + timedelta(minutes=2 * half_life),
        status=ReportStatus.pending.value, contradiction_flag=sanity.contradiction_flag,
        created_by_session=session_id or p.session_id,
        raw_json={"source": "draft" if draft_id else "api", "draft_id": draft_id,
                  "sanity_flags": list(sanity.flags), "sanity": sanity.model_dump(mode="json")})
    _recalculate(report, now)
    session.add(report)
    session.flush()
    written: list[Path] = []
    try:
        if images:
            written = _save_images(session, report, images)
        publish_event(session, EventType.report_created, report_event_payload(report, flags=list(sanity.flags)),
                      report_id=report.report_id)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return report


def create_report(session: Session, user: User, payload: ReportCreate | dict, images: list[ImageUpload] | None = None,
                  session_id: str | None = None) -> Report:
    """Create a `pending` report (never auto-verified). Text-only reports are fine; 1-5 images are optional."""
    try:
        report = _create_report(session, user, payload, images=images, session_id=session_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(report)
    return report


# ============================================================================= votes
def _vote(session: Session, report_id: str, user: User, value: int, event_type: EventType) -> Report:
    _require_active_verified(user)
    if value not in (1, -1):
        raise api_error("VALIDATION_ERROR", "Vote value must be +1 or -1.", "value")
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    if report.status not in rules.LIVE_STATUSES:
        session.commit()
        raise api_error("REPORT_NOT_VOTABLE", f"A {report.status} report cannot be voted on.",
                        details={"status": report.status})
    if report.reported_by == user.id:
        raise api_error("CANNOT_VOTE_OWN_REPORT", "You cannot vote on or confirm your own report.")
    existing = session.exec(select(ReportVote).where(
        ReportVote.report_id == report_id, ReportVote.user_id == user.id)).first()
    if existing is not None and existing.value == value:
        return report  # same vote again: nothing changes
    if existing is not None:
        existing.value = value
        session.add(existing)
    else:
        session.add(ReportVote(report_id=report_id, user_id=user.id, value=value))
    session.flush()
    _recount_votes(session, report)
    if value == 1:  # only an upvote / confirmation refreshes the clock; a downvote never does
        report.last_verified_at = now
        report.expires_at = rules.expiry_for(report)
    _recalculate(report, now)
    session.add(report)
    publish_event(session, event_type, report_event_payload(
        report, value=value, upvotes=report.upvotes, downvotes=report.downvotes,
        community_score=report.community_score, needs_admin_review=report.needs_admin_review), report_id=report_id)
    session.commit()
    session.refresh(report)
    return report


def vote_report(session: Session, report_id: str, user: User, value: int) -> Report:
    return _vote(session, report_id, user, value, EventType.report_voted)


def confirm_report(session: Session, report_id: str, user: User) -> Report:
    """"I can see this too": an upvote that publishes report.confirmed instead of report.voted."""
    return _vote(session, report_id, user, 1, EventType.report_confirmed)


def retract_vote(session: Session, report_id: str, user: User) -> Report:
    _require_active_verified(user)
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    vote = session.exec(select(ReportVote).where(
        ReportVote.report_id == report_id, ReportVote.user_id == user.id)).first()
    if vote is None:
        session.commit()
        raise api_error("NO_VOTE_TO_RETRACT", "You have not voted on this report.")
    if report.status not in rules.LIVE_STATUSES:
        session.commit()
        raise api_error("REPORT_NOT_VOTABLE", f"A {report.status} report cannot be voted on.",
                        details={"status": report.status})
    removed = vote.value
    session.delete(vote)
    session.flush()
    _recount_votes(session, report)  # last_verified_at is not rolled back
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_voted, report_event_payload(
        report, value=0, retracted=removed, upvotes=report.upvotes, downvotes=report.downvotes,
        community_score=report.community_score, needs_admin_review=report.needs_admin_review), report_id=report_id)
    session.commit()
    session.refresh(report)
    return report


def get_user_vote(session: Session, report_id: str, user_id: int) -> int | None:
    vote = session.exec(select(ReportVote).where(
        ReportVote.report_id == report_id, ReportVote.user_id == user_id)).first()
    return vote.value if vote else None


# ============================================================================= resolve & admin
def resolve_report(session: Session, report_id: str, user: User) -> Report:
    """verified -> resolved (a verified user or an admin saying the problem is over). Restores routing."""
    _require_active_verified(user)
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    if report.status != ReportStatus.verified.value:
        session.commit()
        raise _transition_error(report, (ReportStatus.verified.value,), "resolve")
    was_routing = report.affects_routing
    report.status = ReportStatus.resolved.value
    raw = _raw(report)
    raw["resolved_by"] = user.id
    report.raw_json = raw
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_resolved, report_event_payload(report, resolved_by_admin=user.role == "admin"),
                  report_id=report_id)
    if was_routing:
        _publish_recalc(session, report, "report_resolved")
    session.commit()
    session.refresh(report)
    return report


def admin_verify(session: Session, report_id: str, admin: User, note: str | None = None, *,
                 edge_closed: bool | None = None, extra_time_min: int | None = None) -> Report:
    """pending -> verified. The only way a report becomes verified. `edge_closed` / `extra_time_min` are the
    routing effects the admin approves (default none). Sanity flags are acknowledged, not blocking."""
    _require_admin(admin)
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    if report.status != ReportStatus.pending.value:
        session.commit()
        raise _transition_error(report, (ReportStatus.pending.value,), "verify")
    if edge_closed and not report.affected_route_ids_json:
        raise api_error("EDGE_CLOSED_REQUIRES_ROUTES", "edge_closed needs the report to name affected routes.",
                        "edge_closed")
    reporter = session.get(User, report.reported_by)
    sanity = run_sanity_checks(session, ReportCreate(
        report_type=report.report_type, severity=report.severity, affected_hub=report.affected_hub,
        affected_route_ids=report.affected_route_ids_json or [], description=report.description or "-",
        mode=report.mode, fare_amount_bdt=report.fare_amount_bdt, incident_time=report.incident_time),
        reporter, now, stage="verify", exclude_report_id=report.report_id)
    if sanity.errors:
        _raise_first(sanity)

    raw = _raw(report)
    raw["reviewed_flags"] = list(set(raw.get("sanity_flags", [])) | set(sanity.flags))
    raw["sanity_flags"] = []
    raw["sanity_at_verification"] = sanity.model_dump(mode="json")
    raw["verified_by"] = admin.id
    if note:
        raw["admin_note"] = note
    report.raw_json = raw
    report.status = ReportStatus.verified.value
    report.admin_approved = True
    report.last_verified_at = now
    report.expires_at = rules.expiry_for(report)
    if edge_closed is not None:
        report.edge_closed = edge_closed
    if extra_time_min is not None:
        report.extra_time_min = max(0, int(extra_time_min))
    report.contradiction_flag = report.contradiction_flag or sanity.contradiction_flag
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_verified, report_event_payload(
        report, edge_closed=report.edge_closed, extra_time_min=report.extra_time_min), report_id=report_id)
    if report.affects_routing:
        _publish_recalc(session, report, "report_verified")
    session.commit()
    session.refresh(report)
    return report


def admin_reject(session: Session, report_id: str, admin: User, reason: str) -> Report:
    _require_admin(admin)
    if not reason or not reason.strip():
        raise api_error("VALIDATION_ERROR", "A rejection reason is required.", "reason")
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    if report.status not in rules.LIVE_STATUSES:
        session.commit()
        raise _transition_error(report, rules.LIVE_STATUSES, "reject")
    was_routing = report.affects_routing
    raw = _raw(report)
    raw.update(reject_reason=reason.strip(), rejected_by=admin.id, sanity_flags=[])
    report.raw_json = raw
    report.status = ReportStatus.rejected.value
    report.admin_approved = False
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_rejected, report_event_payload(report, reason=reason.strip()),
                  report_id=report_id)
    if was_routing:
        _publish_recalc(session, report, "report_rejected")
    session.commit()
    session.refresh(report)
    return report


def admin_reopen(session: Session, report_id: str, admin: User, reason: str | None = None) -> Report:
    """rejected / resolved / expired -> pending, with a fresh lifetime (the admin says it is current again)."""
    _require_admin(admin)
    report = _get_report(session, report_id)
    now = utcnow()
    refresh_decay(session, report, now)
    allowed = (ReportStatus.rejected.value, ReportStatus.resolved.value, ReportStatus.expired.value)
    if report.status not in allowed:
        session.commit()
        raise _transition_error(report, allowed, "reopen")
    raw = _raw(report)
    raw.update(reopened_by=admin.id, reopened_from=report.status)
    if reason:
        raw["reopen_reason"] = reason
    report.raw_json = raw
    report.status = ReportStatus.pending.value
    report.admin_approved = False
    report.last_verified_at = now
    report.expires_at = rules.expiry_for(report)
    _recalculate(report, now)
    session.add(report)
    publish_event(session, EventType.report_reopened, report_event_payload(report, reason=reason), report_id=report_id)
    session.commit()
    session.refresh(report)
    return report
