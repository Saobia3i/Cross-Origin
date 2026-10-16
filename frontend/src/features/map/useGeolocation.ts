import { useCallback, useEffect, useRef, useState } from 'react';
import type { UserFix } from './types';

export type GeoStatus = 'idle' | 'locating' | 'granted' | 'denied' | 'unavailable' | 'timeout' | 'insecure';
export interface GeoState { status: GeoStatus; fix: UserFix | null }

function request(highAccuracy: boolean, timeout: number) {
  return new Promise<GeolocationPosition>((res, rej) =>
    navigator.geolocation.getCurrentPosition(res, rej, { enableHighAccuracy: highAccuracy, timeout, maximumAge: 30_000 }));
}

/**
 * One-shot location with graceful degradation:
 *  insecure origin → denied (permissions API) → high-accuracy try → low-accuracy retry.
 * No watchPosition on purpose: battery (team doc mentions low-battery users) and no need to follow.
 */
export function useGeolocation(auto: boolean) {
  const [state, setState] = useState<GeoState>({ status: 'idle', fix: null });
  const alive = useRef(true);

  const locate = useCallback(async () => {
    if (!window.isSecureContext) { setState((s) => ({ ...s, status: 'insecure' })); return; }
    if (!('geolocation' in navigator)) { setState((s) => ({ ...s, status: 'unavailable' })); return; }
    setState((s) => ({ ...s, status: 'locating' }));
    try {
      let pos: GeolocationPosition;
      try { pos = await request(true, 8000); }
      catch (e) {
        if ((e as GeolocationPositionError).code === 1) throw e;     // denied → don't retry
        pos = await request(false, 8000);                            // desktop / indoors fallback
      }
      if (!alive.current) return;
      const { latitude: lat, longitude: lng, accuracy } = pos.coords;
      setState({ status: 'granted', fix: { lat, lng, accuracy } });
    } catch (e) {
      if (!alive.current) return;
      const code = (e as GeolocationPositionError).code;
      setState((s) => ({ ...s, status: code === 1 ? 'denied' : code === 3 ? 'timeout' : 'unavailable' }));
    }
  }, []);

  useEffect(() => {
    // StrictMode runs effects twice in dev: keep `alive` symmetric, never skip the 2nd run.
    alive.current = true;
    if (auto) void locate();
    return () => { alive.current = false; };
  }, [auto, locate]);

  return { ...state, locate };
}
