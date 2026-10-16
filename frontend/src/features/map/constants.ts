import type { LatLngBoundsLiteral, LatLngTuple } from 'leaflet';
import type { MapTheme, TransitMode } from './types';

export const DHAKA_CENTER: LatLngTuple = [23.7806, 90.4000];
/** Roadmap-Dhaka.csv spans lat 23.685–23.888, lng 90.334–90.475; padded ~3 km. */
export const DHAKA_BOUNDS: LatLngBoundsLiteral = [[23.655, 90.304], [23.918, 90.505]];

export const TILE = {
  light: { url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', maxZoom: 20, subdomains: 'abcd' },
  dark:  { url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',  maxZoom: 20, subdomains: 'abcd' },
  osm:   { url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',                  maxZoom: 19, subdomains: 'abc'  },
} as const;
export const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

/** z-order (Leaflet default: tiles 200, overlay 400, marker 600). */
export const PANE = { transit: 'dp-transit', closed: 'dp-closed', route: 'dp-route', hubs: 'dp-hubs', alerts: 'dp-alerts', user: 'dp-user' } as const;
export const PANE_Z: Record<string, number> = { [PANE.transit]: 410, [PANE.closed]: 420, [PANE.route]: 430, [PANE.hubs]: 440, [PANE.alerts]: 650, [PANE.user]: 660 };

/** Read mode colours from tokens.css so the map follows whatever palette wins. */
const MODE_VAR: Record<TransitMode, string> = {
  bus: '--color-bus', mrt: '--color-mrt', rickshaw: '--color-rickshaw',
  leguna: '--color-leguna', cng: '--color-cng', walk: '--color-walk',
};
const MODE_FALLBACK: Record<TransitMode, string> = {
  bus: '#1A6FA8', mrt: '#6B3FA0', rickshaw: '#E6B800', leguna: '#0D8A7A', cng: '#2E7D4F', walk: '#7A7668',
};
export function modeColor(mode: TransitMode): string {
  if (typeof window === 'undefined') return MODE_FALLBACK[mode];
  const v = getComputedStyle(document.documentElement).getPropertyValue(MODE_VAR[mode]).trim();
  return v || MODE_FALLBACK[mode];
}

/** red is reserved for blocked/danger (team doc §4.1) — alert pins never use it. */
export const STATUS_COLOR: Record<string, string> = {
  verified: '#16A34A', active: '#16A34A',
  pending: '#F59E0B',
  downvoted: '#94A3B8', flagged: '#94A3B8',
  resolved: '#2563EB', expired: '#94A3B8',
};
export const CLOSED_COLOR = '#E11D48';
export const statusColor = (s: string) => STATUS_COLOR[s] ?? '#94A3B8';

export const themeInk = (t: MapTheme) => (t === 'dark' ? '#0A0F1C' : '#FFFFFF');
