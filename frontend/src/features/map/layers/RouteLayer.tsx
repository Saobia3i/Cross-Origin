import { Fragment, useEffect } from 'react';
import L from 'leaflet';
import type { LatLngTuple } from 'leaflet';
import { CircleMarker, Pane, Polyline, useMap } from 'react-leaflet';
import { PANE, PANE_Z, modeColor, themeInk } from '../constants';
import { mrtSlice } from '../geo';
import type { MapHub, MapLeg, MapRoute, MapTheme } from '../types';

type HubById = Map<string, MapHub>;

export function legPositions(leg: MapLeg, hubs: HubById): LatLngTuple[] | null {
  if (leg.geometry) return leg.geometry;
  const a = hubs.get(leg.fromHubId), b = hubs.get(leg.toHubId);
  if (!a || !b) return null;
  const A: LatLngTuple = [a.lat, a.lng], B: LatLngTuple = [b.lat, b.lng];
  return leg.mode === 'mrt' ? mrtSlice(A, B) : [A, B];
}

function FitRoute({ route, hubs }: { route: MapRoute | null | undefined; hubs: HubById }) {
  const map = useMap();
  const id = route?.id;
  useEffect(() => {                                                   // refit only when the route CHANGES
    if (!route) return;
    const pts = route.legs.flatMap((l) => legPositions(l, hubs) ?? []);
    if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 15, animate: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, map]);
  return null;
}

interface Props { selected?: MapRoute | null; alternative?: MapRoute | null; hubs: HubById; theme: MapTheme }

export default function RouteLayer({ selected, alternative, hubs, theme }: Props) {
  const ink = themeInk(theme);
  const draw = (route: MapRoute, kind: 'sel' | 'alt') => route.legs.map((leg, i) => {
    const pos = legPositions(leg, hubs);
    if (!pos) return null;                                            // unknown hub => draw nothing (grounding)
    const color = modeColor(leg.mode);
    return kind === 'sel' ? (
      <Fragment key={`${route.id}-${i}`}>
        <Polyline positions={pos} interactive={false} pathOptions={{ color: ink, weight: 10, opacity: 0.9, lineCap: 'round' }} />
        <Polyline positions={pos} interactive={false} pathOptions={{ color, weight: 5, opacity: 1, lineCap: 'round' }} />
      </Fragment>
    ) : (
      <Polyline key={`${route.id}-${i}`} positions={pos} interactive={false}
        pathOptions={{ color, weight: 5, opacity: 0.9, dashArray: '2 9', lineCap: 'round' }} />   // dotted = alternative; dashed red = closed
    );
  });
  const transfers = (route: MapRoute) => route.legs.slice(0, -1).map((l, i) => {
    const h = hubs.get(l.toHubId);
    return h ? <CircleMarker key={`t-${route.id}-${i}`} center={[h.lat, h.lng]} radius={6} interactive={false}
      pathOptions={{ color: '#334155', weight: 3, fillColor: '#FFFFFF', fillOpacity: 1 }} /> : null;
  });

  return (
    <Pane name={PANE.route} style={{ zIndex: PANE_Z[PANE.route] }}>
      {alternative && draw(alternative, 'alt')}
      {selected && draw(selected, 'sel')}
      {selected && transfers(selected)}
      <FitRoute route={selected} hubs={hubs} />
    </Pane>
  );
}
