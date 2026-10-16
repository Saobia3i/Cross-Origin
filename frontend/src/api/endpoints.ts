// Endpoint paths follow backend/app/api_catalog.py (served at GET /api/catalog = source of truth).
import apiClient from './client';
import { getSessionId } from '../store/session';
import type {
  NetworkData, Report, TripSearchData, ChatResponse, UserType, TokenResponse,
  EventsData, SimulationResult, ScenarioType,
} from './schemas';

export const fetchCatalog = () => apiClient.get('/api/catalog').then(r => r.data);

// ── Transit ──
export const fetchNetwork = () =>
  apiClient.get<NetworkData>('/api/transit/network').then(r => r.data);

// ── Reports / alerts ──
export const fetchReports = (params?: { status?: string; affected_hub?: string; limit?: number }) =>
  apiClient.get<Report[]>('/api/reports', { params }).then(r => r.data);

export const fetchReportDetail = (id: string) =>
  apiClient.get<Report>(`/api/reports/${id}`).then(r => r.data);

export const voteReport = (id: string, value: 1 | -1) =>
  apiClient.post(`/api/reports/${id}/vote`, { value }).then(r => r.data);

export const retractVote = (id: string) =>
  apiClient.delete(`/api/reports/${id}/vote`).then(r => r.data);

export const confirmReport = (id: string) =>
  apiClient.post(`/api/reports/${id}/confirm`).then(r => r.data);

export const resolveReport = (id: string) =>
  apiClient.post(`/api/reports/${id}/resolve`).then(r => r.data);

export interface ReportInput {
  report_type: string;
  severity: string;
  affected_hub?: string;
  description: string;
  incident_time?: string;
}
/** Creates a report (pending). Optional images go as multipart `images` fields. */
export const createReport = (input: ReportInput, images: File[] = []) => {
  const body = { ...input, session_id: getSessionId() };
  if (images.length === 0) return apiClient.post('/api/reports', body).then(r => r.data);
  const form = new FormData();
  form.append('payload', JSON.stringify(body));
  images.forEach(img => form.append('images', img));
  return apiClient
    .post('/api/reports', form, { headers: { 'Content-Type': 'multipart/form-data' } })
    .then(r => r.data);
};

export const submitDraft = (draftId: string) =>
  apiClient.post(`/api/reports/drafts/${draftId}/submit`).then(r => r.data);

export const cancelDraft = (draftId: string) =>
  apiClient.post(`/api/reports/drafts/${draftId}/cancel`).then(r => r.data);

// ── Trip search ──
export interface TripSearchParams {
  origin: string;
  destination: string;
  budget_bdt?: number;
  prefer_student_fare: boolean;
  avoid_hubs?: string[];
  optimize: 'cheapest' | 'fastest' | 'fewest_transfers' | 'balanced';
}
export const searchTrips = (params: TripSearchParams) =>
  apiClient
    .post<TripSearchData>('/api/trips/search', { ...params, session_id: getSessionId() })
    .then(r => r.data);

export const estimateFare = (routeOptionId: string, preferStudentFare: boolean) =>
  apiClient
    .post('/api/fares/estimate', {
      route_option_id: routeOptionId,
      prefer_student_fare: preferStudentFare,
      session_id: getSessionId(),
    })
    .then(r => r.data);

// ── Simulation (per session) ──
export const runSimulation = (
  scenario_type: ScenarioType,
  opts: { zone_hub?: string; origin?: string; destination?: string; prefer_student_fare?: boolean } = {},
) =>
  apiClient
    .post<SimulationResult>('/api/simulations', { session_id: getSessionId(), scenario_type, ...opts })
    .then(r => r.data);

export const resetSimulation = () =>
  apiClient.post('/api/simulations/reset', { session_id: getSessionId() }).then(r => r.data);

// ── Auth ──
export const login = (email: string, password: string) =>
  apiClient.post<TokenResponse>('/api/auth/login', { email, password }).then(r => r.data);

export const register = (name: string, email: string, password: string) =>
  apiClient.post<TokenResponse>('/api/auth/register', { name, email, password }).then(r => r.data);

export const getMe = () => apiClient.get<UserType>('/api/auth/me').then(r => r.data);

// ── Admin (report moderation is implemented; route/hub/fare-rule admin endpoints answer 501) ──
export const verifyReport = (
  id: string,
  body: { note?: string; edge_closed?: boolean; extra_time_min?: number } = {},
) => apiClient.post(`/api/admin/reports/${id}/verify`, body).then(r => r.data);

export const rejectReport = (id: string, reason: string) =>
  apiClient.post(`/api/admin/reports/${id}/reject`, { reason }).then(r => r.data);

export const reopenReport = (id: string, reason?: string) =>
  apiClient.post(`/api/admin/reports/${id}/reopen`, { reason }).then(r => r.data);

// ── Chat ──
export const sendChat = (message: string, uiLanguage: 'en' | 'bn') =>
  apiClient
    .post<ChatResponse>('/api/chat', { session_id: getSessionId(), message, ui_language: uiLanguage })
    .then(r => r.data);

// ── Events ──
export const fetchEvents = (sinceId: number, limit = 100) =>
  apiClient
    .get<EventsData>('/api/events', { params: { since_id: sinceId, session_id: getSessionId(), limit } })
    .then(r => r.data);

// ── Admin: users, roads, fare rules ──
export interface AdminUser {
  id: number; name: string; email: string; role: 'user' | 'admin'; is_verified: boolean; status: string;
}
export const adminListUsers = () => apiClient.get<AdminUser[]>('/api/admin/users').then(r => r.data);
export const adminUserAction = (id: number, action: 'verify' | 'disable' | 'enable') =>
  apiClient.post<AdminUser>(`/api/admin/users/${id}/${action}`).then(r => r.data);
export const adminBlockRoute = (routeId: string, reason?: string) =>
  apiClient.post(`/api/admin/routes/${encodeURIComponent(routeId)}/block`, { reason }).then(r => r.data);
export const adminUnblockRoute = (routeId: string) =>
  apiClient.post(`/api/admin/routes/${encodeURIComponent(routeId)}/unblock`).then(r => r.data);
