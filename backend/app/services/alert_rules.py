"""Pure alert maths: half-life, decay, confidence, routing eligibility. No database access.

    base_confidence = severity_base + 0.05*upvotes - 0.08*downvotes + (0.20 if verified)
    decay_factor    = 0.5 ** (age_min / half_life_min)      age = now - (last_verified_at or created_at)
    confidence      = clamp(base_confidence * decay_factor, 0, 1)     freshness = decay_factor
    expires_at      = (last_verified_at or created_at) + 2 * half_life_min

A report affects routing only if it is verified, confidence >= 0.40, now < expires_at, and it names a hub
or routes. rejected / resolved / expired / draft reports have confidence 0 and never affect routing.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.enums import ReportStatus, ReportType, Severity
from app.models import Report

HALF_LIFE_MIN = {
    ReportType.waterlogging.value: 60,
    ReportType.severe_gridlock.value: 30,
    ReportType.intersection_blockade.value: 30,
    ReportType.road_construction.value: 1440,
    ReportType.fare_surge.value: 45,
    ReportType.accident.value: 45,
    ReportType.all_clear.value: 60,
    ReportType.other.value: 60,
}
SEVERITY_BASE = {
    Severity.light.value: 0.25,
    Severity.moderate.value: 0.35,
    Severity.heavy.value: 0.45,
    Severity.critical.value: 0.55,
}
UPVOTE_WEIGHT = 0.05
DOWNVOTE_WEIGHT = 0.08
ADMIN_BONUS = 0.20
ROUTING_CONFIDENCE_THRESHOLD = 0.40
INACTIVE_STATUSES = {
    ReportStatus.rejected.value, ReportStatus.resolved.value, ReportStatus.expired.value, ReportStatus.draft.value,
}
LIVE_STATUSES = (ReportStatus.pending.value, ReportStatus.verified.value)


def half_life_for(report_type: str) -> int:
    return HALF_LIFE_MIN[report_type]


def reference_time(report: Report) -> datetime:
    return report.last_verified_at or report.created_at


def expiry_for(report: Report) -> datetime:
    return reference_time(report) + timedelta(minutes=2 * report.half_life_min)


def decay_factor(report: Report, now: datetime) -> float:
    age_min = max(0.0, (now - reference_time(report)).total_seconds() / 60)
    return 0.5 ** (age_min / report.half_life_min)


def base_confidence(report: Report) -> float:
    base = SEVERITY_BASE[report.severity] + UPVOTE_WEIGHT * report.upvotes - DOWNVOTE_WEIGHT * report.downvotes
    return base + (ADMIN_BONUS if report.status == ReportStatus.verified.value else 0.0)


def compute_confidence(report: Report, now: datetime) -> float:
    if report.status in INACTIVE_STATUSES:
        return 0.0
    return min(1.0, max(0.0, base_confidence(report) * decay_factor(report, now)))


def targets_routing(report: Report) -> bool:
    return bool(report.affected_hub or report.affected_route_ids_json)


def affects_routing(report: Report, now: datetime) -> bool:
    return (report.status == ReportStatus.verified.value
            and compute_confidence(report, now) >= ROUTING_CONFIDENCE_THRESHOLD
            and now < report.expires_at
            and targets_routing(report))


def apply_decay(report: Report, now: datetime) -> Report:
    """Refresh the derived columns in place (confidence, freshness, community_score, affects_routing).
    Status and expires_at are managed by the service, not here."""
    report.community_score = report.upvotes - report.downvotes
    report.confidence = compute_confidence(report, now)
    report.freshness = 0.0 if report.status == ReportStatus.expired.value else decay_factor(report, now)
    report.affects_routing = affects_routing(report, now)
    return report
