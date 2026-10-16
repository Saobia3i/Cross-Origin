"""SQLModel tables. Enum-like columns are plain strings guarded by CHECK constraints
(allowed values live in app/enums.py). *_json columns are JSON."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import NaiveDatetime
from sqlalchemy import JSON, CheckConstraint, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.enums import (
    ChatRole, Direction, DraftStatus, EventType, HubType, MultiplierAppliesTo, ReportStatus,
    ReportType, RidesharePostStatus, RouteEdgeStatus, ScenarioType, Severity, SimulationStatus,
    TransitMode, UserRole, UserStatus, values,
)


def utcnow() -> datetime:
    """Naive UTC (SQLite has no tz support)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _in(column: str, enum_cls: type[Enum]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values(enum_cls))
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{column}_{enum_cls.__name__.lower()}")


def _json(default: Any = None, nullable: bool = False):
    col = Column(JSON(none_as_null=True), nullable=nullable)
    if callable(default):
        return Field(default_factory=default, sa_column=col)
    return Field(default=default, sa_column=col)


# ----------------------------------------------------------------------------- users
class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (_in("role", UserRole), _in("status", UserStatus))

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    password_hash: str
    role: str = UserRole.user.value
    is_verified: bool = False
    status: str = UserStatus.active.value
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------- transit network (from datasets)
class Hub(SQLModel, table=True):
    __tablename__ = "hubs"
    __table_args__ = (_in("type", HubType),)

    hub_id: str = Field(primary_key=True)
    name: str
    name_bn: Optional[str] = None
    aliases_json: list = _json(list)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    type: str = HubType.hub.value
    wealth_index: Optional[float] = None
    area_wealth_note: Optional[str] = None
    source_dataset: str = ""
    raw_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class RouteEdge(SQLModel, table=True):
    __tablename__ = "route_edges"
    __table_args__ = (_in("mode", TransitMode), _in("direction", Direction), _in("status", RouteEdgeStatus))

    route_id: str = Field(primary_key=True)
    mode: str
    provider: Optional[str] = None
    category: Optional[str] = None
    origin_hub: str = Field(foreign_key="hubs.hub_id", index=True)
    destination_hub: str = Field(foreign_key="hubs.hub_id", index=True)
    direction: str = Direction.unknown.value
    status: str = RouteEdgeStatus.active.value
    closed_reason: Optional[str] = None
    road_distance_km: Optional[float] = None
    straight_line_km: Optional[float] = None
    time_normal_min: Optional[float] = None
    time_peak_min: Optional[float] = None
    time_rain_min: Optional[float] = None
    time_detour_min: Optional[float] = None
    boarding_notes_json: list = _json(list)
    alighting_notes_json: list = _json(list)
    transfer_notes_json: list = _json(list)
    geometry_json: Optional[dict] = _json(None, nullable=True)
    stops_json: Optional[list] = _json(None, nullable=True)
    flood_prone: bool = False
    construction_prone: bool = False
    student_eligible: bool = False
    source_dataset: str = ""
    raw_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class FareMatrixRow(SQLModel, table=True):
    """One row of Dhaka_Transport_Fares."""
    __tablename__ = "fare_matrix_rows"
    __table_args__ = (_in("mode", TransitMode),)

    id: Optional[int] = Field(default=None, primary_key=True)
    mode: str
    category: Optional[str] = None
    provider: Optional[str] = None
    from_hub: Optional[str] = Field(default=None, index=True)
    to_hub: Optional[str] = Field(default=None, index=True)
    road_distance_km: Optional[float] = None
    time_normal_min: Optional[float] = None
    time_peak_hour_min: Optional[float] = None
    time_rain_min: Optional[float] = None
    metro_stations: Optional[str] = None
    wealth_index_from: Optional[float] = None
    wealth_index_to: Optional[float] = None
    area_wealth_multiplier: Optional[float] = None
    area_wealth_note: Optional[str] = None
    normal_low_bdt: Optional[float] = None
    normal_high_bdt: Optional[float] = None
    rain_low_bdt: Optional[float] = None
    rain_high_bdt: Optional[float] = None
    peak_office_hour_low_bdt: Optional[float] = None
    peak_office_hour_high_bdt: Optional[float] = None
    peak_plus_rain_low_bdt: Optional[float] = None
    peak_plus_rain_high_bdt: Optional[float] = None
    blockage_construction_waterlog_detour_low_bdt: Optional[float] = None
    blockage_construction_waterlog_detour_high_bdt: Optional[float] = None
    regular_holiday_low_bdt: Optional[float] = None
    regular_holiday_high_bdt: Optional[float] = None
    festive_holiday_low_bdt: Optional[float] = None
    festive_holiday_high_bdt: Optional[float] = None
    student_fare_low_bdt: Optional[float] = None
    student_fare_high_bdt: Optional[float] = None
    student_rule: Optional[str] = None
    notes: Optional[str] = None
    raw_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class MetroFareMatrix(SQLModel, table=True):
    __tablename__ = "metro_fare_matrix"
    __table_args__ = (UniqueConstraint("from_station_hub_id", "to_station_hub_id", name="uq_metro_pair"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    from_station_hub_id: str = Field(foreign_key="hubs.hub_id", index=True)
    to_station_hub_id: str = Field(foreign_key="hubs.hub_id", index=True)
    fare_bdt: float
    source_dataset: str = ""
    raw_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ModeRate(SQLModel, table=True):
    __tablename__ = "mode_rates"
    __table_args__ = (_in("mode", TransitMode),)

    mode: str = Field(primary_key=True)
    base_fare_bdt: Optional[float] = None
    per_km_bdt: Optional[float] = None
    min_fare_bdt: Optional[float] = None
    average_speed_kmh: Optional[float] = None
    low_band_multiplier: Optional[float] = None
    high_band_multiplier: Optional[float] = None
    raw_json: dict = _json(dict)


class ScenarioMultiplier(SQLModel, table=True):
    """scenario uses the dataset's names: normal, rain, peak, peak_plus_rain, detour,
    regular_holiday, festive_holiday. multiplier is NULL when the dataset does not state one."""
    __tablename__ = "scenario_multipliers"
    __table_args__ = (
        UniqueConstraint("mode", "scenario", name="uq_mode_scenario"),
        _in("mode", TransitMode), _in("applies_to", MultiplierAppliesTo),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    mode: str
    scenario: str
    multiplier: Optional[float] = None
    applies_to: str = MultiplierAppliesTo.fare.value
    raw_json: dict = _json(dict)


class WealthIndex(SQLModel, table=True):
    __tablename__ = "wealth_indices"

    hub_name: str = Field(primary_key=True)
    hub_id: Optional[str] = Field(default=None, foreign_key="hubs.hub_id", index=True)
    wealth_index: float
    note: Optional[str] = None
    raw_json: dict = _json(dict)


class FareRule(SQLModel, table=True):
    """Admin-editable per-mode rules."""
    __tablename__ = "fare_rules"
    __table_args__ = (_in("mode", TransitMode),)

    mode: str = Field(primary_key=True)
    student_discount_percent: float = 0
    minimum_student_fare_bdt: Optional[float] = None
    surge_allowed: bool = False
    use_official_matrix: bool = False
    allow_formula_fallback: bool = True
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------- reports
class Report(SQLModel, table=True):
    __tablename__ = "reports"
    __table_args__ = (_in("status", ReportStatus), _in("report_type", ReportType), _in("severity", Severity))

    report_id: str = Field(primary_key=True)
    report_type: str
    severity: str
    affected_hub: Optional[str] = Field(default=None, foreign_key="hubs.hub_id", index=True)
    affected_route_ids_json: list = _json(list)
    description: str
    mode: Optional[str] = None
    fare_amount_bdt: Optional[float] = None
    reported_by: int = Field(foreign_key="users.id", index=True)
    incident_time: NaiveDatetime = Field(default_factory=utcnow)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_verified_at: Optional[NaiveDatetime] = None
    upvotes: int = 0
    downvotes: int = 0
    confidence: float = 0.0
    freshness: float = 1.0
    half_life_min: int
    expires_at: NaiveDatetime
    status: str = Field(default=ReportStatus.pending.value, index=True)
    edge_closed: bool = False
    extra_time_min: int = 0
    needs_admin_review: bool = False
    contradiction_flag: bool = False
    community_score: int = 0
    admin_approved: bool = False
    affects_routing: bool = False
    created_by_session: Optional[str] = None
    raw_json: Optional[dict] = _json(None, nullable=True)


class ReportImage(SQLModel, table=True):
    __tablename__ = "report_images"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_id: str = Field(foreign_key="reports.report_id", index=True)
    file_path: str
    mime: str
    size_bytes: int
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ReportVote(SQLModel, table=True):
    __tablename__ = "report_votes"
    __table_args__ = (
        UniqueConstraint("report_id", "user_id", name="uq_vote_report_user"),
        CheckConstraint("value IN (-1, 1)", name="ck_vote_value"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    report_id: str = Field(foreign_key="reports.report_id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    value: int
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ReportDraft(SQLModel, table=True):
    __tablename__ = "report_drafts"
    __table_args__ = (_in("status", DraftStatus),)

    draft_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    payload_json: dict = _json(dict)
    status: str = DraftStatus.draft.value
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    submitted_at: Optional[NaiveDatetime] = None


# ----------------------------------------------------------------------------- simulation & events
class Simulation(SQLModel, table=True):
    """At most one active simulation per session: enforced in service logic, not by the DB."""
    __tablename__ = "simulations"
    __table_args__ = (_in("status", SimulationStatus), _in("scenario_type", ScenarioType))

    scenario_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    scenario_type: str
    zone_hub: Optional[str] = Field(default=None, foreign_key="hubs.hub_id")
    status: str = SimulationStatus.active.value
    effects_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: Optional[NaiveDatetime] = None
    reset_at: Optional[NaiveDatetime] = None


class Event(SQLModel, table=True):
    __tablename__ = "events"
    __table_args__ = (_in("event_type", EventType),)

    event_id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str
    session_id: Optional[str] = Field(default=None, index=True)
    report_id: Optional[str] = Field(default=None, index=True)
    route_id: Optional[str] = None
    payload_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------- chat
class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    session_id: str = Field(primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (_in("role", ChatRole),)

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="chat_sessions.session_id", index=True)
    role: str
    content: str
    intent: Optional[str] = None
    entities_json: Optional[dict] = _json(None, nullable=True)
    payload_json: Optional[dict] = _json(None, nullable=True)
    grounding_json: Optional[dict] = _json(None, nullable=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ChatContext(SQLModel, table=True):
    __tablename__ = "chat_context"

    session_id: str = Field(primary_key=True, foreign_key="chat_sessions.session_id")
    context_state_json: dict = _json(dict)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class LlmCache(SQLModel, table=True):
    __tablename__ = "llm_cache"

    id: Optional[int] = Field(default=None, primary_key=True)
    prompt_hash: str = Field(unique=True, index=True)
    provider: str
    model: str
    response_json: dict = _json(dict)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------- ride-share
class RidesharePost(SQLModel, table=True):
    __tablename__ = "rideshare_posts"
    __table_args__ = (_in("status", RidesharePostStatus), _in("mode", TransitMode))

    post_id: str = Field(primary_key=True)
    origin_hub: Optional[str] = Field(default=None, foreign_key="hubs.hub_id")
    destination_hub: Optional[str] = Field(default=None, foreign_key="hubs.hub_id")
    hub_id: Optional[str] = Field(default=None, foreign_key="hubs.hub_id")
    mode: str
    created_by: int = Field(foreign_key="users.id", index=True)
    seats_total: int
    seats_taken: int = 1
    estimated_total_fare_min_bdt: Optional[float] = None
    estimated_total_fare_max_bdt: Optional[float] = None
    scenario: Optional[str] = None
    status: str = RidesharePostStatus.active.value
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: Optional[NaiveDatetime] = None
    notes: Optional[str] = None


class RideshareJoin(SQLModel, table=True):
    __tablename__ = "rideshare_joins"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_join_post_user"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    post_id: str = Field(foreign_key="rideshare_posts.post_id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    joined_at: NaiveDatetime = Field(default_factory=utcnow)


ALL_TABLES = [
    User, Hub, RouteEdge, FareMatrixRow, MetroFareMatrix, ModeRate, ScenarioMultiplier, WealthIndex,
    FareRule, Report, ReportImage, ReportVote, ReportDraft, Simulation, Event, ChatSession,
    ChatMessage, ChatContext, LlmCache, RidesharePost, RideshareJoin,
]
