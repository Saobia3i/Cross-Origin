import type { LatLngTuple } from 'leaflet';
import type { MapAlert, MapEdge, MapHub, MapLeg, MapRoute, TransitMode } from './types';

/**
 * THE ONLY FILE THAT KNOWS ABOUT BACKEND SHAPES.
 * responses.md, api/schemas.ts and store/trip.ts disagree on names today
 * (lat/lng vs latitude/longitude, from vs from_hub, {alerts:[]} vs [] ...).
 * Every accessor below accepts both so a contract change = edit this file only.
 */
type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === 'object' && v !== null;
const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
const MODES: TransitMode[] = ['bus', 'mrt', 'rickshaw', 'leguna', 'cng', 'walk'];
const asMode = (v: unknown): TransitMode => (MODES.includes(v as TransitMode) ? (v as TransitMode) : 'bus');
export const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');

/** Accept an array, or an envelope-unwrapped object like { alerts: [...] } / { routes: [...] }. */
export function unwrapList(raw: unknown, key: string): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (isRec(raw) && Array.isArray(raw[key])) return raw[key] as unknown[];
  return [];
}

export function toMapHubs(raw: unknown): MapHub[] {
  return unwrapList(raw, 'hubs').flatMap((r): MapHub[] => {
    if (!isRec(r)) return [];
    const id = str(r.hub_id) ?? str(r.id);
    const lat = num(r.lat) ?? num(r.latitude);
    const lng = num(r.lng) ?? num(r.longitude);
    if (!id || lat === undefined || lng === undefined) return [];     // never draw what we can't place
    return [{
      id, lat, lng,
      name: str(r.name) ?? id,
      nameBn: str(r.name_bn),
      type: str(r.type) ?? 'hub',
      aliases: Array.isArray(r.aliases) ? r.aliases.filter((a): a is string => typeof a === 'string') : [],
    }];
  });
}

export function makeHubIndex(hubs: MapHub[]) {
  const byKey = new Map<string, MapHub>();
  for (const h of hubs) {
    byKey.set(norm(h.id), h);
    byKey.set(norm(h.name), h);
    h.aliases.forEach((a) => byKey.set(norm(a), h));
  }
  return { get: (idOrName: string | undefined) => (idOrName ? byKey.get(norm(idOrName)) : undefined) };
}
export type HubIndex = ReturnType<typeof makeHubIndex>;

export function toMapEdges(raw: unknown): MapEdge[] {
  return unwrapList(raw, 'routes').flatMap((r): MapEdge[] => {
    if (!isRec(r)) return [];
    const id = str(r.route_id) ?? str(r.id);
    const from = str(r.origin_hub) ?? str(r.from);
    const to = str(r.destination_hub) ?? str(r.to);
    if (!id || !from || !to) return [];
    return [{ id, from, to, mode: asMode(r.mode), closed: r.status === 'closed', geometry: asLine(r.geometry) }];
  });
}

export function toMapAlerts(raw: unknown): MapAlert[] {
  return unwrapList(raw, 'alerts').flatMap((r): MapAlert[] => {
    if (!isRec(r)) return [];
    const id = str(r.alert_id) ?? str(r.id);
    const hubId = str(r.affected_hub);
    if (!id || !hubId) return [];
    const impact = isRec(r.routing_impact) ? r.routing_impact : {};
    const edgeIds = (Array.isArray(r.affected_route_ids) ? r.affected_route_ids : Array.isArray(r.affected_routes) ? r.affected_routes : [])
      .filter((x): x is string => typeof x === 'string');
    const closes = r.edge_closed === true || impact.edge_closed === true;
    return [{
      id, hubId, type: str(r.type) ?? 'other', severity: str(r.severity) ?? 'medium',
      status: str(r.status) ?? 'pending', description: str(r.description) ?? '',
      closedEdgeIds: closes ? edgeIds : [],
    }];
  });
}

/** GeoJSON-ish or [[lat,lng],…] → Leaflet [lat,lng][]. Returns undefined if not recognisable. */
function asLine(g: unknown): LatLngTuple[] | undefined {
  if (!Array.isArray(g) || g.length < 2) return undefined;
  const pts = g.filter((p): p is number[] => Array.isArray(p) && p.length >= 2 && typeof p[0] === 'number' && typeof p[1] === 'number');
  return pts.length >= 2 ? pts.map((p) => [p[0], p[1]] as LatLngTuple) : undefined;
}

/** Accepts store RouteOption (from/to/…), responses.md route option (from_hub/to_hub/…), or {routes:[…]}[0]. */
export function toMapRoute(raw: unknown, idx: HubIndex): MapRoute | null {
  if (!isRec(raw) || !Array.isArray(raw.legs)) return null;
  const legs = raw.legs.flatMap((l): MapLeg[] => {
    if (!isRec(l)) return [];
    const from = idx.get(str(l.from_hub) ?? str(l.fromHub) ?? str(l.from));
    const to = idx.get(str(l.to_hub) ?? str(l.toHub) ?? str(l.to));
    if (!from || !to) return [];                                     // unknown hub => skip leg, never invent
    return [{ mode: asMode(l.mode), fromHubId: from.id, toHubId: to.id, routeId: str(l.route_id) ?? str(l.routeId), geometry: asLine(l.geometry) }];
  });
  return legs.length ? { id: str(raw.route_option_id) ?? str(raw.id) ?? 'route', legs } : null;
}

/**
 * Simulation response → map overlay. Accepts responses.md §3.2
 * ({broken_segments, affected_segments[{route_id,effect}], recalculated_routes[]}).
 * Anything else (e.g. the frontend's old mock {affected:string[], detour:string}) → no overlay.
 */
export function toMapSimulation(raw: unknown, idx: HubIndex): { closedRouteIds: string[]; alternativeRoute: MapRoute | null } {
  if (!isRec(raw)) return { closedRouteIds: [], alternativeRoute: null };
  const ids = new Set<string>();
  if (Array.isArray(raw.broken_segments)) raw.broken_segments.forEach((x) => { if (typeof x === 'string') ids.add(x); });
  if (Array.isArray(raw.affected_segments)) raw.affected_segments.forEach((x) => {
    if (isRec(x) && x.effect === 'closed' && typeof x.route_id === 'string') ids.add(x.route_id);
  });
  const first = Array.isArray(raw.recalculated_routes) ? raw.recalculated_routes[0] : undefined;
  return { closedRouteIds: [...ids], alternativeRoute: toMapRoute(first, idx) };
}
