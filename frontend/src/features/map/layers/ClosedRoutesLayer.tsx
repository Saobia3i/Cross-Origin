import { Pane, Polyline, Tooltip } from 'react-leaflet';
import { CLOSED_COLOR, PANE, PANE_Z } from '../constants';
import type { MapEdge, MapHub } from '../types';

interface Props { edges: MapEdge[]; closedIds: string[]; hubs: Map<string, MapHub>; label: string; onRouteClick?: (id: string) => void }

export default function ClosedRoutesLayer({ edges, closedIds, hubs, label, onRouteClick }: Props) {
  const ids = new Set([...closedIds, ...edges.filter((e) => e.closed).map((e) => e.id)]);
  return (
    <Pane name={PANE.closed} style={{ zIndex: PANE_Z[PANE.closed] }}>
      {[...ids].map((id) => {
        const e = edges.find((x) => x.id === id);
        const a = e && hubs.get(e.from), b = e && hubs.get(e.to);
        const pos = e?.geometry ?? (a && b ? [[a.lat, a.lng], [b.lat, b.lng]] as [number, number][] : null);
        if (!e || !pos) return null;                                  // id not in the graph => don't draw it
        return (
          <Polyline key={id} positions={pos} bubblingMouseEvents={false}
            pathOptions={{ color: CLOSED_COLOR, weight: 6, opacity: 0.95, dashArray: '10 8' }}
            eventHandlers={{ click: () => onRouteClick?.(id) }}>
            <Tooltip sticky>{label}: {id}</Tooltip>
          </Polyline>
        );
      })}
    </Pane>
  );
}
