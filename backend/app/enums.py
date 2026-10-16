"""Canonical enums. Values are stored as plain strings in SQLite (with CHECK constraints)."""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


def values(enum_cls: type[Enum]) -> list[str]:
    return [m.value for m in enum_cls]


class UserRole(StrEnum):
    user = "user"
    admin = "admin"


class UserStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class HubType(StrEnum):
    hub = "hub"
    metro_station = "metro_station"
    interchange = "interchange"
    landmark = "landmark"


class TransitMode(StrEnum):
    local_bus = "local_bus"
    mrt = "mrt"
    leguna = "leguna"
    rickshaw = "rickshaw"
    cng = "cng"
    walk = "walk"
    car = "car"
    motorbike = "motorbike"
    pathao_bike = "pathao_bike"
    pathao_car = "pathao_car"
    uber_car = "uber_car"
    uber_moto = "uber_moto"


class RouteEdgeStatus(StrEnum):
    active = "active"
    closed = "closed"


class Direction(StrEnum):
    bidirectional = "bidirectional"
    one_way = "one_way"
    unknown = "unknown"


class ReportStatus(StrEnum):
    draft = "draft"
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
    resolved = "resolved"
    expired = "expired"


class ReportType(StrEnum):
    waterlogging = "waterlogging"
    road_construction = "road_construction"
    severe_gridlock = "severe_gridlock"
    intersection_blockade = "intersection_blockade"
    fare_surge = "fare_surge"
    accident = "accident"
    all_clear = "all_clear"
    other = "other"


class Severity(StrEnum):
    light = "light"
    moderate = "moderate"
    heavy = "heavy"
    critical = "critical"


class SimulationStatus(StrEnum):
    active = "active"
    reset = "reset"
    expired = "expired"


class ScenarioType(StrEnum):
    heavy_rain = "heavy_rain"
    peak_rush_hour = "peak_rush_hour"
    blocked_intersection = "blocked_intersection"
    regular_holiday = "regular_holiday"
    festive_holiday = "festive_holiday"
    detour = "detour"


class EventType(StrEnum):
    report_draft_created = "report.draft.created"
    report_draft_submitted = "report.draft.submitted"
    report_created = "report.created"
    report_voted = "report.voted"
    report_confirmed = "report.confirmed"
    report_resolved = "report.resolved"
    report_verified = "report.verified"
    report_rejected = "report.rejected"
    report_reopened = "report.reopened"
    report_expired = "report.expired"
    simulation_applied = "simulation.applied"
    simulation_reset = "simulation.reset"
    route_recalculated = "route.recalculated"
    grounding_blocked = "grounding.blocked"
    chat_message = "chat.message"
    rideshare_created = "rideshare.created"
    rideshare_joined = "rideshare.joined"
    rideshare_left = "rideshare.left"
    rideshare_cancelled = "rideshare.cancelled"


class HubDerivedStatus(StrEnum):
    """Derived at query time from reports/edges; never stored."""
    normal = "normal"
    delayed = "delayed"
    blocked = "blocked"


class DraftStatus(StrEnum):
    draft = "draft"
    submitted = "submitted"
    cancelled = "cancelled"
    expired = "expired"


class ChatRole(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class RidesharePostStatus(StrEnum):
    active = "active"
    full = "full"
    cancelled = "cancelled"
    expired = "expired"


class MultiplierAppliesTo(StrEnum):
    fare = "fare"
    time = "time"
    distance = "distance"
