import type { LatLngTuple } from 'leaflet';
import { DHAKA_BOUNDS } from './constants';
import type { MapHub } from './types';
import transit from './data/transit-data.json';

export const inDhaka = (lat: number, lng: number) =>
  lat >= DHAKA_BOUNDS[0][0] && lat <= DHAKA_BOUNDS[1][0] && lng >= DHAKA_BOUNDS[0][1] && lng <= DHAKA_BOUNDS[1][1];

export function haversineM(a: LatLngTuple, b: LatLngTuple): number {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLng = (b[1] - a[1]) * rad;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

export function nearestHub(lat: number, lng: number, hubs: MapHub[]): { hub: MapHub; meters: number } | null {
  let best: { hub: MapHub; meters: number } | null = null;
  for (const hub of hubs) {
    const meters = haversineM([lat, lng], [hub.lat, hub.lng]);
    if (!best || meters < best.meters) best = { hub, meters };
  }
  return best;
}

/** Zoom that fits the GPS accuracy radius (desktop Wi-Fi/IP fixes can be km wide). */
export const zoomForAccuracy = (m: number) => (m <= 300 ? 15 : m <= 1500 ? 14 : 13);

// ── Reference network from transit-data.json (lat,lng order — Leaflet-ready) ──
export const MRT_STATIONS = transit.metro.stations;
export const MRT_LINE: LatLngTuple[] = MRT_STATIONS.map((s) => [s.lat, s.lng]);
export const BUS_LINES = transit.buses.map((b) => ({
  id: b.id, name: b.name,
  stops: b.stops,
  path: b.segments.flatMap((s) => s.coordinates as LatLngTuple[]),
}));

/** Slice the MRT-6 polyline between the stations nearest to two points. */
export function mrtSlice(from: LatLngTuple, to: LatLngTuple): LatLngTuple[] {
  const near = (p: LatLngTuple) => MRT_LINE.reduce((bi, q, i) => (haversineM(p, q) < haversineM(p, MRT_LINE[bi]) ? i : bi), 0);
  const i = near(from), j = near(to);
  if (i === j) return [from, to];
  const seg = i < j ? MRT_LINE.slice(i, j + 1) : MRT_LINE.slice(j, i + 1).reverse();
  return [from, ...seg, to];
}
