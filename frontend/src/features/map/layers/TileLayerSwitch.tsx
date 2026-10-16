import { useRef, useState } from 'react';
import { TileLayer } from 'react-leaflet';
import { ATTRIBUTION, TILE } from '../constants';
import type { MapTheme } from '../types';

export type TileHealth = 'carto' | 'osm' | 'down';

/** CARTO (theme aware) → OSM standard → none (vectors keep working = schematic view). */
export default function TileLayerSwitch({ theme, onHealth }: { theme: MapTheme; onHealth: (h: TileHealth) => void }) {
  // CARTO's free basemaps now answer with "API KEY REQUIRED" placeholder tiles (they load fine, so no tileerror) → start on OSM.
  const [provider, setProvider] = useState<TileHealth>('osm');
  const errors = useRef(0);
  const loaded = useRef(0);

  const advance = () => {
    const next: TileHealth = 'down';
    errors.current = 0; loaded.current = 0;
    setProvider(next); onHealth(next);
  };
  if (provider === 'down') return null;
  const cfg = provider === 'osm' ? TILE.osm : TILE[theme];

  return (
    <TileLayer
      key={`${provider}-${theme}`}
      url={cfg.url} maxZoom={cfg.maxZoom} subdomains={cfg.subdomains} attribution={ATTRIBUTION}
      eventHandlers={{
        tileload: () => { loaded.current += 1; },
        tileerror: () => { errors.current += 1; if (loaded.current === 0 && errors.current >= 4) advance(); },
      }}
    />
  );
}
