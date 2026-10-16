import { CircleMarker, Pane, Tooltip } from 'react-leaflet';
import { PANE, PANE_Z, themeInk } from '../constants';
import type { MapHub, MapTheme } from '../types';

interface Props { hubs: MapHub[]; highlight: string[]; theme: MapTheme; onHubClick?: (id: string) => void }

export default function HubsLayer({ hubs, highlight, theme, onHubClick }: Props) {
  const hi = new Set(highlight);
  return (
    <Pane name={PANE.hubs} style={{ zIndex: PANE_Z[PANE.hubs] }}>
      {hubs.map((h) => {
        const on = hi.has(h.id);
        return (
          <CircleMarker key={h.id} center={[h.lat, h.lng]}
            radius={on ? 10 : h.type === 'interchange' ? 7 : 5}
            bubblingMouseEvents={false}          // else a hub click ALSO fires map click ("report here")
            pathOptions={{ color: on ? '#E6B800' : themeInk(theme) === '#FFFFFF' ? '#334155' : '#E6EDF7', weight: on ? 4 : 2, fillColor: themeInk(theme), fillOpacity: 1 }}
            eventHandlers={{ click: () => onHubClick?.(h.id) }}>
            <Tooltip direction="top" offset={[0, -6]} permanent={on}>{h.name}</Tooltip>
          </CircleMarker>
        );
      })}
    </Pane>
  );
}
