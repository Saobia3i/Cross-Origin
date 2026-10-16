import L from 'leaflet';
import { Marker, Pane, Tooltip } from 'react-leaflet';
import { PANE, PANE_Z, statusColor } from '../constants';
import type { MapAlert, MapHub } from '../types';

const HIDDEN = new Set(['resolved', 'expired']);
const iconCache = new Map<string, L.DivIcon>();
function pin(status: string, severity: string) {
  const key = `${status}|${severity}`;
  let ic = iconCache.get(key);
  if (!ic) {
    const big = severity === 'high' || severity === 'heavy' || severity === 'critical';
    const s = big ? 32 : 26;
    ic = L.divIcon({
      className: 'dp-pin', iconSize: [s, s], iconAnchor: [s / 2, s / 2],
      html: `<span class="dp-pin__dot" style="width:${s}px;height:${s}px;background:${statusColor(status)}">!</span>`,
    });
    iconCache.set(key, ic);
  }
  return ic;
}

interface Props { alerts: MapAlert[]; hubs: Map<string, MapHub>; resolveHub: (id: string) => MapHub | undefined; onAlertClick?: (id: string) => void }

export default function AlertsLayer({ alerts, resolveHub, onAlertClick }: Props) {
  const seen = new Map<string, number>();
  return (
    <Pane name={PANE.alerts} style={{ zIndex: PANE_Z[PANE.alerts] }}>
      {alerts.filter((a) => !HIDDEN.has(a.status)).map((a) => {
        const h = resolveHub(a.hubId);
        if (!h) return null;
        const n = seen.get(h.id) ?? 0; seen.set(h.id, n + 1);        // fan out alerts that share a hub
        const ang = (n * 2 * Math.PI) / 3, d = n === 0 ? 0 : 0.0009;
        return (
          <Marker key={a.id} position={[h.lat + d * Math.cos(ang), h.lng + d * Math.sin(ang)]} icon={pin(a.status, a.severity)}
            eventHandlers={{ click: () => onAlertClick?.(a.id) }}>
            <Tooltip direction="top" offset={[0, -12]}>{a.type} · {a.status}</Tooltip>
          </Marker>
        );
      })}
    </Pane>
  );
}
