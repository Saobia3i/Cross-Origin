"""Pydantic request/response schemas.

Conventions (see docs/BACKEND_CONTRACT.md): every response is wrapped in the envelope;
rickshaw/CNG and ride-share per-person fares are ranges; chat returns `reply_text`.
"""
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.enums import (
    ChatRole, Direction, DraftStatus, EventType, HubDerivedStatus, HubType, ReportStatus, ReportType,
    RidesharePostStatus, RouteEdgeStatus, ScenarioType, Severity, SimulationStatus, TransitMode,
    UserRole, UserStatus,
)


# Plain pattern, not EmailStr: demo accounts use the reserved ".local" domain, which strict
# email validators reject.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- envelope --------------------------------------------------------------
class ErrorItem(BaseModel):
    code: str
    message: str
    field: Optional[str] = None
    details: dict[str, Any] = {}


class Meta(BaseModel):
    request_id: str
    timestamp: str
    source: str


class Envelope(BaseModel):
    ok: bool
    data: Any = None
    meta: Meta
    errors: list[ErrorItem] = []


def _round2(v: float) -> float:
    return round(v, 2)


# Full precision in memory; rounded to 2 decimals whenever serialized (JSON / model_dump).
Money = Annotated[float, PlainSerializer(_round2, return_type=float)]

FareSource = Literal["metro_matrix", "explicit_fare_row", "formula", "walk", "manual_admin"]


class FareRange(BaseModel):
    """A fare is always a min/max range; `is_range` is False only for fixed fares (metro, walk)."""
    min_bdt: Money
    max_bdt: Money
    currency: str = "BDT"
    is_range: bool
    source: FareSource
    notes: list[str] = []


class LegFareResult(BaseModel):
    route_id: str
    mode: str
    regular_fare_min_bdt: Money
    regular_fare_max_bdt: Money
    student_fare_min_bdt: Money
    student_fare_max_bdt: Money
    student_discount_applies: bool
    scenario: str
    distance_km: Optional[Money] = None
    fare_source: FareSource
    warnings: list[str] = []


class TripFareResult(BaseModel):
    total_regular_fare_min_bdt: Money
    total_regular_fare_max_bdt: Money
    total_student_fare_min_bdt: Money
    total_student_fare_max_bdt: Money
    currency: str = "BDT"
    legs: list[LegFareResult]
    warnings: list[str] = []
    student_discount_applies_any_leg: bool


class RideShareFareResult(BaseModel):
    participants: int
    total_fare_min_bdt: Money
    total_fare_max_bdt: Money
    per_person_min_bdt: Money
    per_person_max_bdt: Money
    suggested_safe_per_person_bdt: int
    currency: str = "BDT"
    note: str = ("Per-person amount is an estimate range split equally. "
                 "Suggested safe amount is upper-bound ceil.")


class FareSanityResult(BaseModel):
    mode: str
    submitted_fare_bdt: Money
    checked: bool  # False when there was not enough data to judge
    is_realistic: bool
    needs_admin_review: bool  # never blocks report creation; just flags it
    expected: Optional[FareRange] = None
    reasons: list[str] = []


# ---- auth ------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    password: str


class UserPublic(ORM):
    id: int
    email: str
    name: str
    role: UserRole
    is_verified: bool
    status: UserStatus


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


# ---- transit ---------------------------------------------------------------
class HubOut(BaseModel):
    """Hub with its derived (never stored) status."""
    hub_id: str
    name: str
    name_bn: Optional[str] = None
    aliases: list[str] = []
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    type: HubType
    status: HubDerivedStatus = HubDerivedStatus.normal
    status_reason: Optional[str] = None
    active_alert_count: int = 0


class HubStatusOut(BaseModel):
    hub_id: str
    status: HubDerivedStatus
    reason: Optional[str] = None
    active_alert_ids: list[str] = []


class RouteEdgeOut(BaseModel):
    route_id: str
    mode: TransitMode
    origin_hub: str
    destination_hub: str
    direction: Direction
    status: RouteEdgeStatus  # effective for the caller's session (admin + verified alerts + simulation)
    closed_reason: Optional[str] = None
    closed_by: Optional[Literal["admin", "verified_alert", "simulation"]] = None
    road_distance_km: Optional[float] = None
    time_normal_min: Optional[float] = None
    extra_time_min: float = 0
    boarding_notes: list = []
    alighting_notes: list = []
    student_eligible: bool = False
    provider: Optional[str] = None
    category: Optional[str] = None
    flood_prone: bool = False
    construction_prone: bool = False
    active_alert_ids: list[str] = []
    geometry: Optional[dict] = None  # only with ?include_geometry=true


class FareRuleOut(ORM):
    mode: str
    student_discount_percent: float
    minimum_student_fare_bdt: Optional[float] = None
    surge_allowed: bool
    use_official_matrix: bool
    allow_formula_fallback: bool


class ModeRateOut(ORM):
    mode: str
    base_fare_bdt: Optional[float] = None
    per_km_bdt: Optional[float] = None
    min_fare_bdt: Optional[float] = None
    average_speed_kmh: Optional[float] = None
    low_band_multiplier: Optional[float] = None
    high_band_multiplier: Optional[float] = None


class ScenarioMultiplierOut(ORM):
    mode: str
    scenario: str
    multiplier: Optional[float] = None  # null = the dataset states none
    applies_to: str


class NetworkOut(BaseModel):
    hubs: list[HubOut]
    routes: list[RouteEdgeOut]
    fare_rules: list[FareRuleOut]
    mode_rates: list[ModeRateOut]
    scenario_multipliers: list[ScenarioMultiplierOut]


class RouteEdgeCreate(BaseModel):
    route_id: Optional[str] = None
    mode: TransitMode
    origin_hub: str
    destination_hub: str
    direction: Direction = Direction.unknown
    road_distance_km: Optional[float] = Field(default=None, ge=0)
    time_normal_min: Optional[float] = Field(default=None, ge=0)


class RouteEdgeUpdate(BaseModel):
    status: Optional[RouteEdgeStatus] = None
    closed_reason: Optional[str] = None
    direction: Optional[Direction] = None
    road_distance_km: Optional[float] = Field(default=None, ge=0)
    time_normal_min: Optional[float] = Field(default=None, ge=0)
    time_peak_min: Optional[float] = Field(default=None, ge=0)
    time_rain_min: Optional[float] = Field(default=None, ge=0)
    flood_prone: Optional[bool] = None
    construction_prone: Optional[bool] = None


# ---- reports ---------------------------------------------------------------
class ReportCreate(BaseModel):
    report_type: ReportType
    severity: Severity
    affected_hub: Optional[str] = None
    affected_route_ids: list[str] = []
    description: str = Field(min_length=1, max_length=2000)
    mode: Optional[TransitMode] = None
    fare_amount_bdt: Optional[float] = Field(default=None, ge=0)
    incident_time: Optional[datetime] = None
    session_id: Optional[str] = Field(default=None, max_length=100)

    @field_validator("affected_hub", mode="before")
    @classmethod
    def _blank_hub(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class ReportImageOut(ORM):
    id: int
    url: str
    mime: str
    size_bytes: int


class ReportOut(ORM):
    report_id: str
    report_type: ReportType
    severity: Severity
    affected_hub: Optional[str] = None
    affected_route_ids: list[str] = Field(default=[], validation_alias="affected_route_ids_json")
    description: str
    mode: Optional[str] = None
    fare_amount_bdt: Optional[float] = None
    reported_by: int
    incident_time: datetime
    created_at: datetime
    last_verified_at: Optional[datetime] = None
    upvotes: int = 0
    downvotes: int = 0
    confidence: float = 0.0
    freshness: float = 1.0
    half_life_min: int
    expires_at: datetime
    status: ReportStatus
    edge_closed: bool = False
    extra_time_min: int = 0
    needs_admin_review: bool = False
    contradiction_flag: bool = False
    community_score: int = 0
    admin_approved: bool = False
    affects_routing: bool = False
    my_vote: Optional[int] = None  # +1 / -1 for the logged-in caller, else null
    images: list[ReportImageOut] = []
    flags: list[str] = []  # unreviewed sanity flags: duplicate, contradiction, unrealistic_fare


class VoteRequest(BaseModel):
    value: int

    @field_validator("value")
    @classmethod
    def _pm1(cls, v: int) -> int:
        if v not in (-1, 1):
            raise ValueError("value must be -1 or 1")
        return v


class AdminVerifyRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    edge_closed: Optional[bool] = None  # routing effects the admin approves; default none
    extra_time_min: Optional[int] = Field(default=None, ge=0, le=1440)


class AdminRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AdminReopenRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)


class VoteOut(ORM):
    report_id: str
    user_id: int
    value: int


class DraftCreate(BaseModel):
    session_id: str
    payload: ReportCreate


class DraftOut(ORM):
    draft_id: str
    session_id: str
    user_id: Optional[int] = None
    payload: dict = Field(default={}, validation_alias="payload_json")
    status: DraftStatus
    created_at: datetime
    submitted_at: Optional[datetime] = None


# ---- simulation -------------------------------------------------------------
class SimulationCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    scenario_type: ScenarioType
    zone_hub: Optional[str] = Field(default=None, max_length=120)
    # optional: the journey to compare before/after (defaults to the session's last trip search)
    origin: Optional[str] = Field(default=None, max_length=120)
    destination: Optional[str] = Field(default=None, max_length=120)
    prefer_student_fare: Optional[bool] = None


class SimulationResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


class ScenarioEffects(BaseModel):
    scenario_type: str
    fare_scenario: str
    time_scenario: Optional[str] = None
    zone_hub: Optional[str] = None
    closed_route_ids: list[str] = []
    closure_reasons: dict[str, str] = {}
    time_overrides: dict[str, float] = {}  # route_id -> dataset scenario travel time (min)
    extra_transfer_wait_min: float = 0
    warnings: list[str] = []


class SimulationOut(ORM):
    scenario_id: str
    session_id: str
    scenario_type: ScenarioType
    zone_hub: Optional[str] = None
    status: SimulationStatus
    effects: dict = Field(default={}, validation_alias="effects_json")
    created_at: datetime
    expires_at: Optional[datetime] = None
    reset_at: Optional[datetime] = None


# ---- events -------------------------------------------------------------------
class EventOut(ORM):
    event_id: int
    event_type: EventType
    session_id: Optional[str] = None
    report_id: Optional[str] = None
    route_id: Optional[str] = None
    payload: dict = Field(default={}, validation_alias="payload_json")
    created_at: datetime


class DebugEventPublish(BaseModel):
    secret: Optional[str] = None  # or the X-Demo-Secret header
    event_type: EventType
    session_id: Optional[str] = None
    report_id: Optional[str] = None
    route_id: Optional[str] = None
    payload: dict = {}


# ---- chat -----------------------------------------------------------------------
ChatIntentName = Literal[
    "find_route", "request_alternative_route", "compare_fare", "submit_disruption_report", "confirm_report_draft",
    "explain_fare_rule", "alert_reliability", "apply_simulation", "reset_simulation", "smalltalk", "unknown"]
UiLanguage = Literal["en", "bn", "banglish"]


class ChatEntities(BaseModel):
    """What the user said, as text. Hub names here are NOT yet resolved or trusted (the backend resolves them)."""
    model_config = ConfigDict(extra="ignore")
    origin: Optional[str] = None
    destination: Optional[str] = None
    budget_bdt: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    prefer_student_fare: Optional[bool] = None
    avoid_hubs: list[str] = Field(default_factory=list, max_length=10)
    avoid_modes: list[str] = Field(default_factory=list, max_length=12)
    optimize: Optional[Literal["cheapest", "fastest", "fewest_transfers", "balanced"]] = None
    mode: Optional[str] = None
    report_type: Optional[Literal["waterlogging", "road_construction", "severe_gridlock", "intersection_blockade",
                                  "fare_surge", "accident", "all_clear", "other"]] = None
    severity: Optional[Literal["light", "moderate", "heavy", "critical"]] = None
    hub: Optional[str] = None
    fare_amount_bdt: Optional[float] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=500)
    scenario_type: Optional[Literal["heavy_rain", "peak_rush_hour", "blocked_intersection", "regular_holiday",
                                    "festive_holiday"]] = None
    zone_hub: Optional[str] = None
    report_id: Optional[str] = None
    confirm: Optional[bool] = None   # confirm_report_draft: True = submit, False = cancel
    fare_focus: Optional[bool] = None


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: ChatIntentName = "unknown"
    entities: ChatEntities = Field(default_factory=ChatEntities)
    language: UiLanguage = "en"
    confidence: float = Field(default=0.5, ge=0, le=1)
    source: str = "rules"  # rules | llm:<provider>
    normalized_text: str = ""


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    ui_language: Optional[UiLanguage] = None


class ChatAction(BaseModel):
    type: Literal["show_route_options", "show_fare_breakdown", "show_draft_report", "confirm_draft_report",
                  "show_reliability", "highlight_map_route", "apply_simulation"]
    payload_key: str


class ChatGrounding(BaseModel):
    checked: bool = True
    valid: bool
    violations: list[dict] = []
    fallback_used: bool = False


class ChatResponse(BaseModel):
    session_id: str
    message_id: str
    reply_text: str  # never `reply.text`
    language_detected: UiLanguage = "en"
    intent: str
    entities: dict = {}
    actions: list[ChatAction] = []
    structured_payload: dict = {}
    grounding: ChatGrounding
    context_state: dict = {}
    suggested_followups: list[str] = []


class ChatMessageOut(ORM):
    id: int
    session_id: str
    role: ChatRole
    content: str
    intent: Optional[str] = None
    entities: Optional[dict] = Field(default=None, validation_alias="entities_json")
    structured_payload: Optional[dict] = Field(default=None, validation_alias="payload_json")
    grounding: Optional[dict] = Field(default=None, validation_alias="grounding_json")
    created_at: datetime


# ---- ride-share --------------------------------------------------------------------
class RideshareCreate(BaseModel):
    origin_hub: Optional[str] = None
    destination_hub: Optional[str] = None
    hub_id: Optional[str] = None
    mode: TransitMode
    seats_total: int = Field(ge=2, le=8)
    scenario: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class RidesharePostOut(ORM):
    post_id: str
    origin_hub: Optional[str] = None
    destination_hub: Optional[str] = None
    hub_id: Optional[str] = None
    mode: TransitMode
    created_by: int
    seats_total: int
    seats_taken: int
    estimated_total_fare_min_bdt: Optional[float] = None
    estimated_total_fare_max_bdt: Optional[float] = None
    per_person_fare: Optional[FareRange] = None  # total range / seats_taken, always a range
    scenario: Optional[str] = None
    status: RidesharePostStatus
    created_at: datetime
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


# ---- routing -----------------------------------------------------------------------
class RouteLeg(BaseModel):
    order: int
    mode: str
    route_id: str
    from_hub: str
    to_hub: str
    regular_fare_min_bdt: Optional[Money] = None  # None only when the dataset has no fare for this leg
    regular_fare_max_bdt: Optional[Money] = None
    student_fare_min_bdt: Optional[Money] = None
    student_fare_max_bdt: Optional[Money] = None
    student_discount_applies: bool = False
    travel_time_min: Optional[Money] = None
    transfer_wait_after_min: Money = 0
    walking_time_min: Money = 0
    boarding_instruction: Optional[str] = None
    alighting_instruction: Optional[str] = None
    transfer_instruction: Optional[str] = None
    warnings: list[str] = []


class RouteGrounding(BaseModel):
    valid: bool
    checked_route_ids: bool
    checked_hub_ids: bool
    checked_fares: bool


class RouteOption(BaseModel):
    route_option_id: str
    rank_label: str  # cheapest | fastest | fewest_transfers | balanced | alternative
    also_best_for: list[str] = []
    total_regular_fare_min_bdt: Optional[Money] = None
    total_regular_fare_max_bdt: Optional[Money] = None
    total_student_fare_min_bdt: Optional[Money] = None
    total_student_fare_max_bdt: Optional[Money] = None
    total_travel_time_min: Optional[Money] = None
    total_transfer_wait_min: Money = 0
    total_walking_time_min: Money = 0
    total_duration_min: Optional[Money] = None
    transfers: int = 0
    leg_count: int = 0
    within_budget: bool = True
    disruption_risk: Literal["low", "medium", "high"] = "low"
    scenario: str = "normal"
    legs: list[RouteLeg] = []
    warnings: list[str] = []
    grounding: RouteGrounding


class NoRouteSuggestion(BaseModel):
    type: Literal["increase_budget", "remove_avoided_hub", "remove_avoided_mode", "widen_time"]
    message: str
    value: Any = None


class NoRouteReason(BaseModel):
    code: str
    message: str
    suggestions: list[NoRouteSuggestion] = []


# ---- trip search & fare estimate ----------------------------------------------------
OPTIMIZE = Literal["cheapest", "fastest", "fewest_transfers", "balanced"]


class TripSearchRequest(BaseModel):
    """Structured fields, or `query_text` for natural language (needs nlp_service)."""
    origin: Optional[str] = Field(default=None, max_length=120)
    destination: Optional[str] = Field(default=None, max_length=120)
    budget_bdt: Optional[int] = Field(default=None, ge=0)
    strict_budget: bool = False
    prefer_student_fare: bool = False
    avoid_hubs: list[str] = Field(default=[], max_length=20)
    avoid_modes: list[str] = Field(default=[], max_length=12)
    optimize: OPTIMIZE = "balanced"
    session_id: Optional[str] = Field(default=None, max_length=100)
    scenario: str = "normal"  # normal, rain, peak, peak_plus_rain, detour, regular_holiday, festive_holiday
    query_text: Optional[str] = Field(default=None, max_length=500)

    @field_validator("origin", "destination", "query_text", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _need_route_or_text(self):
        if not self.query_text and not (self.origin and self.destination):
            raise ValueError("provide origin and destination, or query_text")
        return self


class ActiveScenarioOut(BaseModel):
    scenario_id: str
    scenario_type: str
    zone_hub: Optional[str] = None
    effects: dict = {}
    created_at: datetime
    expires_at: Optional[datetime] = None


class TripSearchData(BaseModel):
    query_echo: dict
    routes: list[RouteOption]
    no_route_reason: Optional[NoRouteReason] = None
    session_id: Optional[str] = None
    active_scenario: Optional[ActiveScenarioOut] = None
    origin: Optional[dict] = None
    destination: Optional[dict] = None
    optimize: str
    scenario: str
    disruptions: dict = {}
    warnings: list[str] = []
    warning_codes: list[str] = []
    grounding: Optional[dict] = None  # grounding badge: every option was checked against the engines


class FareLegRef(BaseModel):
    route_id: str
    mode: Optional[str] = None  # checked against the route if given


class FareEstimateRequest(BaseModel):
    route_option_id: Optional[str] = None
    legs: list[FareLegRef] = Field(default=[], max_length=30)
    prefer_student_fare: bool = False
    scenario: str = "normal"
    session_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _need_legs_or_option(self):
        if not self.route_option_id and not self.legs:
            raise ValueError("provide route_option_id or legs")
        return self


class FareEstimateData(TripFareResult):
    route_option_id: Optional[str] = None
    scenario: str = "normal"
    prefer_student_fare: bool = False
    session_id: Optional[str] = None
    legs_source: Literal["cached_route_option", "request_legs"]


# ---- grounding ----------------------------------------------------------------------
class GroundingCheckRequest(BaseModel):
    """Exactly one of route_option / chat_response."""
    route_option: Optional[dict] = None
    chat_response: Optional[dict] = None
    scenario: Optional[str] = None
    session_id: Optional[str] = Field(default=None, max_length=100)
    secret: Optional[str] = None  # or the X-Demo-Secret header

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.route_option is None) == (self.chat_response is None):
            raise ValueError("provide exactly one of route_option or chat_response")
        return self


class FakeGroundingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")  # AI-style extras (legs, alert ids...) are checked too
    route_ids: list[str] = []
    claimed_fare_bdt: Optional[float] = None
    claimed_duration_min: Optional[float] = None
    hub_ids: list[str] = []
    scenario: Optional[str] = None
    session_id: Optional[str] = Field(default=None, max_length=100)
    secret: Optional[str] = None


# ---- ride-share --------------------------------------------------------------
class RideShareCreate(BaseModel):
    mode: str
    origin_hub: Optional[str] = Field(default=None, max_length=120)
    destination_hub: Optional[str] = Field(default=None, max_length=120)
    hub_id: Optional[str] = Field(default=None, max_length=120)
    seats_total: int = Field(ge=2, le=8)
    scenario: str = "normal"
    notes: Optional[str] = Field(default=None, max_length=500)
    expires_in_minutes: int = Field(default=120, ge=5, le=1440)


class RideShareFilters(BaseModel):
    hub_id: Optional[str] = None
    origin_hub: Optional[str] = None
    destination_hub: Optional[str] = None
    mode: Optional[str] = None
    status: Optional[str] = None
    active_only: bool = True


class RideShareSplit(BaseModel):
    """Equal split of the estimated total; every money field is null when no fare estimate exists."""
    participants: int
    total_fare_min_bdt: Optional[Money] = None
    total_fare_max_bdt: Optional[Money] = None
    per_person_min_bdt: Optional[Money] = None
    per_person_max_bdt: Optional[Money] = None
    suggested_safe_per_person_bdt: Optional[int] = None
    currency: str = "BDT"
    note: str = "Estimate split equally. Suggested safe amount is upper-bound ceil."
