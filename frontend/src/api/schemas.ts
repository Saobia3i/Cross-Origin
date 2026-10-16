// Types mirror the backend contract (backend/app/schemas.py, backend/docs/*.md). Backend owns the shapes.

export type TransitMode =
  | 'local_bus' | 'mrt' | 'leguna' | 'rickshaw' | 'cng' | 'walk'
  | 'car' | 'motorbike' | 'pathao_bike' | 'pathao_car' | 'uber_car' | 'uber_moto';

export type HubStatus = 'normal' | 'delayed' | 'blocked';

export interface Hub {
  hub_id: string;
  name: string;
  name_bn?: string | null;
  aliases: string[];
  latitude: number | null;
  longitude: number | null;
  type: string;
  status: HubStatus;
  status_reason?: string | null;
  active_alert_count: number;
}

export interface RouteEdge {
  route_id: string;
  mode: TransitMode;
  origin_hub: string;
  destination_hub: string;
  direction: string;
  status: 'active' | 'closed';
  closed_reason?: string | null;
  road_distance_km?: number | null;
  time_normal_min?: number | null;
  extra_time_min: number;
  boarding_notes: string[];
  alighting_notes: string[];
  student_eligible: boolean;
  provider?: string | null;
  geometry?: unknown;
}

export interface NetworkData {
  hubs: Hub[];
  routes: RouteEdge[];
  fare_rules: unknown[];
  mode_rates: unknown[];
  scenario_multipliers: unknown[];
}

// ── Reports (canonical statuses: draft, pending, verified, rejected, resolved, expired) ──
export type ReportStatus = 'draft' | 'pending' | 'verified' | 'rejected' | 'resolved' | 'expired';
export type ReportType =
  | 'waterlogging' | 'road_construction' | 'severe_gridlock' | 'intersection_blockade'
  | 'fare_surge' | 'accident' | 'all_clear' | 'other';
export type Severity = 'light' | 'moderate' | 'heavy';

export interface Report {
  report_id: string;
  report_type: ReportType;
  severity: Severity;
  affected_hub?: string | null;
  affected_route_ids: string[];
  description: string;
  mode?: string | null;
  fare_amount_bdt?: number | null;
  reported_by: number;
  incident_time: string;
  created_at: string;
  last_verified_at?: string | null;
  upvotes: number;
  downvotes: number;
  confidence: number;
  freshness: number;
  expires_at: string;
  status: ReportStatus;
  edge_closed: boolean;
  extra_time_min: number;
  needs_admin_review: boolean;
  community_score: number;
  affects_routing: boolean;
  my_vote: 1 | -1 | null;
  images: { id: number; url: string; mime: string; size_bytes: number }[];
  flags: string[];
}

// ── Trip search / fares (all fares are ranges) ──
export interface RouteLeg {
  order: number;
  mode: TransitMode;
  route_id: string;
  from_hub: string;
  to_hub: string;
  regular_fare_min_bdt: number;
  regular_fare_max_bdt: number;
  student_fare_min_bdt: number;
  student_fare_max_bdt: number;
  student_discount_applies: boolean;
  travel_time_min: number;
  transfer_wait_after_min: number;
  walking_time_min: number;
  boarding_instruction?: string | null;
  alighting_instruction?: string | null;
  transfer_instruction?: string | null;
  warnings: string[];
}

export interface RouteOption {
  route_option_id: string;
  rank_label: string;
  also_best_for: string[];
  total_regular_fare_min_bdt: number;
  total_regular_fare_max_bdt: number;
  total_student_fare_min_bdt: number;
  total_student_fare_max_bdt: number;
  total_travel_time_min: number;
  total_duration_min: number;
  transfers: number;
  leg_count: number;
  within_budget: boolean;
  disruption_risk: string;
  scenario: string;
  legs: RouteLeg[];
  warnings: string[];
  grounding: { valid: boolean };
}

export interface TripSearchData {
  query_echo: Record<string, unknown>;
  routes: RouteOption[];
  [k: string]: unknown;
}

// ── Chat ──
export interface ChatResponse {
  session_id: string;
  message_id: string;
  reply_text: string;
  intent: string;
  entities: Record<string, unknown>;
  structured_payload: Record<string, unknown> & { routes?: RouteOption[] };
  grounding: { checked: boolean; valid: boolean; violations: unknown[]; fallback_used: boolean };
  context_state: Record<string, unknown>;
  suggested_followups?: string[];
}

// ── Events ──
export interface ApiEvent {
  event_id: number;
  event_type: string;
  session_id?: string | null;
  report_id?: string | null;
  route_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface EventsData {
  events: ApiEvent[];
  latest_id: number;
  next_since_id: number;
  has_more: boolean;
}

// ── Auth ──
export interface UserType {
  id: number;
  name: string;
  email: string;
  role: 'user' | 'admin';
  is_verified: boolean;
  status: string;
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserType;
}

// ── Simulation ──
export type ScenarioType =
  | 'heavy_rain' | 'peak_rush_hour' | 'blocked_intersection'
  | 'regular_holiday' | 'festive_holiday' | 'detour';
export interface SimulationResult {
  scenario_id: string;
  session_id: string;
  scenario_type: ScenarioType;
  status: string;
  zone_hub?: string | null;
  broken_segments: unknown[];
  recalculated_routes: RouteOption[];
  before_after_comparison: {
    available: boolean;
    rerouted?: boolean;
    original_duration_min?: number;
    original_fare_min?: number;
    original_fare_max?: number;
    new_duration_min?: number;
    new_fare_min?: number;
    new_fare_max?: number;
    added_time_min?: number;
    note?: string | null;
    [k: string]: unknown;
  };
}
