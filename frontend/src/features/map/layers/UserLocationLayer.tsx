import { useEffect, useRef } from 'react';
import { Circle, CircleMarker, Pane, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import { PANE, PANE_Z } from '../constants';
import { inDhaka, zoomForAccuracy } from '../geo';
import type { UserFix } from '../types';

interface Props { fix: UserFix | null; centerNonce: number; label: string }

/** Static "you are here" dot + accuracy ring (no pulsing: team motion policy). */
export default function UserLocationLayer({ fix, centerNonce, label }: Props) {
  const map = useMap();
  const autoDone = useRef(false);
  const userDragged = useRef(false);
  const lastNonce = useRef(0);
  useMapEvents({ dragstart: () => { userDragged.current = true; } });

  useEffect(() => {
    if (!fix) return;
    const go = () => map.setView([fix.lat, fix.lng], zoomForAccuracy(fix.accuracy), { animate: false });
    if (centerNonce !== lastNonce.current) { lastNonce.current = centerNonce; go(); return; }   // explicit button → always
    if (!autoDone.current) {                                                                   // first fix on landing
      autoDone.current = true;
      if (inDhaka(fix.lat, fix.lng) && !userDragged.current) go();                              // outside Dhaka → keep Dhaka view
    }
  }, [fix, centerNonce, map]);

  if (!fix) return null;
  return (
    <Pane name={PANE.user} style={{ zIndex: PANE_Z[PANE.user] }}>
      <Circle center={[fix.lat, fix.lng]} radius={fix.accuracy} interactive={false}
        pathOptions={{ color: '#2563EB', weight: 1, fillColor: '#2563EB', fillOpacity: 0.12 }} />
      <CircleMarker center={[fix.lat, fix.lng]} radius={8} bubblingMouseEvents={false}
        pathOptions={{ color: '#FFFFFF', weight: 3, fillColor: '#2563EB', fillOpacity: 1 }}>
        <Tooltip direction="top" offset={[0, -8]}>{label}</Tooltip>
      </CircleMarker>
    </Pane>
  );
}
