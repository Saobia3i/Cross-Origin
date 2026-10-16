import { CircleMarker, Pane, Polyline } from 'react-leaflet';
import { PANE, PANE_Z, modeColor } from '../constants';
import { BUS_LINES, MRT_LINE, MRT_STATIONS } from '../geo';

/** Static reference network from Map_Data/json/transit-data.json (20 KB, 519 points). Non-interactive. */
export default function TransitOverlayLayer() {
  return (
    <Pane name={PANE.transit} style={{ zIndex: PANE_Z[PANE.transit] }}>
      {BUS_LINES.map((b) => (
        <Polyline key={b.id} positions={b.path} interactive={false}
          pathOptions={{ color: modeColor('bus'), weight: 3, opacity: 0.45 }} />
      ))}
      <Polyline positions={MRT_LINE} interactive={false} pathOptions={{ color: modeColor('mrt'), weight: 4, opacity: 0.75 }} />
      {MRT_STATIONS.map((s) => (
        <CircleMarker key={s.id} center={[s.lat, s.lng]} radius={3} interactive={false}
          pathOptions={{ color: modeColor('mrt'), weight: 2, fillColor: '#FFFFFF', fillOpacity: 1 }} />
      ))}
    </Pane>
  );
}
