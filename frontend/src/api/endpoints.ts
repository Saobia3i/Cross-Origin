import apiClient from './client';
import type { Hub, RouteEdge, Alert, RouteOption, FareEstimate, ChatResponse, UserType } from './schemas';

// ── Transit ────────────────────────────────────────────────────────
export const fetchNetwork = () =>
  apiClient.get<{ hubs: Hub[]; routes: RouteEdge[] }>('/api/transit/network').then(r => r.data);

// ── Alerts ─────────────────────────────────────────────────────────
// Accepts a bare array or the responses.md payload {alerts:[…]}. AlertsFeed dereferences images/votes unguarded,
// so missing fields are defaulted here (stop-gap until the backend sends them — docx §7.3 #4).
const withAlertDefaults = (a: Alert): Alert =>
  Object.assign({ images: [], upvotes: 0, downvotes: 0, my_vote: null, comments_count: 0 }, a);

export const fetchAlerts = () =>
  apiClient.get<Alert[] | { alerts: Alert[] }>('/api/alerts/active')
    .then(r => (Array.isArray(r.data) ? r.data : r.data.alerts ?? []).map(withAlertDefaults));

export const voteAlert = (alertId: string, value: 1 | -1) =>
  apiClient.post(`/api/reports/${alertId}/vote`, { value }).then(r => r.data);

export const retractVote = (alertId: string) =>
  apiClient.delete(`/api/reports/${alertId}/vote`).then(r => r.data);

export const fetchAlertComments = (alertId: string) =>
  apiClient.get(`/api/reports/${alertId}/comments`).then(r => r.data);

export const postAlertComment = (alertId: string, text: string) =>
  apiClient.post(`/api/reports/${alertId}/comments`, { text }).then(r => r.data);

// ── Trip search ────────────────────────────────────────────────────
export interface TripSearchParams {
  origin:       string;
  destination:  string;
  budget?:      number;
  student_mode: boolean;
  avoid_hubs?:  string[];
  optimize:     'cheapest' | 'fastest' | 'fewest';
}
export const searchTrips = (params: TripSearchParams) =>
  apiClient.post<RouteOption[] | { routes: RouteOption[] }>('/api/trips/search', params)
    .then(r => (Array.isArray(r.data) ? r.data : r.data.routes ?? []));

// ── Fare estimate ──────────────────────────────────────────────────
export const estimateFare = (routeId: string, studentMode: boolean) =>
  apiClient.post<FareEstimate>('/api/fares/estimate', { route_id: routeId, student_mode: studentMode }).then(r => r.data);

// ── Reports ────────────────────────────────────────────────────────
export const createReport = (form: FormData) =>
  apiClient.post('/api/reports', form, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data);

export const fetchReports = (params?: { status?: string; hub?: string }) =>
  apiClient.get('/api/reports', { params }).then(r => r.data);

export const fetchReportDetail = (id: string) =>
  apiClient.get(`/api/reports/${id}`).then(r => r.data);

// ── Simulation ─────────────────────────────────────────────────────
export const runSimulation = (type: string, zone_hub?: string) =>
  apiClient.post('/api/simulations', { type, zone_hub }).then(r => r.data);

export const resetSimulation = () =>
  apiClient.post('/api/simulations/reset').then(r => r.data);

// ── Auth ───────────────────────────────────────────────────────────
export const login = (email: string, password: string) =>
  apiClient.post('/api/auth/login', { email, password }).then(r => r.data);

export const register = (name: string, email: string, password: string, phone?: string) =>
  apiClient.post('/api/auth/register', { name, email, password, phone }).then(r => r.data);

export const getMe = () =>
  apiClient.get<UserType>('/api/auth/me').then(r => r.data);

// ── Admin ──────────────────────────────────────────────────────────
export const moderateAlert = (alertId: string, action: string, note?: string) =>
  apiClient.patch(`/api/admin/alerts/${alertId}/moderate`, { action, note }).then(r => r.data);

export const blockRoad = (routeId: string, reason: string) =>
  apiClient.post(`/api/admin/roads/${routeId}/block`, { reason }).then(r => r.data);

export const unblockRoad = (routeId: string) =>
  apiClient.post(`/api/admin/roads/${routeId}/unblock`).then(r => r.data);

export const addRoad = (data: Partial<RouteEdge>) =>
  apiClient.post('/api/admin/roads', data).then(r => r.data);

export const editRoad = (routeId: string, data: Partial<RouteEdge>) =>
  apiClient.patch(`/api/admin/roads/${routeId}`, data).then(r => r.data);

export const fetchAdminFares = () =>
  apiClient.get('/api/admin/fares').then(r => r.data);

export const updateFare = (mode: string, data: unknown) =>
  apiClient.put(`/api/admin/fares/${mode}`, data).then(r => r.data);

export const fetchApplications = () =>
  apiClient.get('/api/admin/applications').then(r => r.data);

export const reviewApplication = (id: number, action: 'approve' | 'reject') =>
  apiClient.patch(`/api/admin/applications/${id}`, { action }).then(r => r.data);

// ── Admin application (public) ─────────────────────────────────────
export const applyForAdmin = (data: unknown) =>
  apiClient.post('/api/admin-applications', data).then(r => r.data);

// ── Chat ───────────────────────────────────────────────────────────
export const sendChat = (sessionId: string | null, message: string, uiLanguage: 'en' | 'bn') =>
  apiClient.post<ChatResponse>('/api/chat', { session_id: sessionId, message, ui_language: uiLanguage }).then(r => r.data);
