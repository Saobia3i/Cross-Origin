import type { LatLngTuple } from 'leaflet';

/** Same literals as ModePill / RouteEdgeSchema — keep in sync. */
export type TransitMode = 'bus' | 'mrt' | 'rickshaw' | 'leguna' | 'cng' | 'walk';
export type MapTheme = 'light' | 'dark';

/** Map-local DTOs. The map NEVER imports API/zod types — adapters.ts converts. */
export interface MapHub {
  id: string;            // hub_id, e.g. MIRPUR_10
  name: string;
  nameBn?: string;
  lat: number;
  lng: number;
  type: string;
  aliases: string[];
  approx?: boolean;      // position is an approximate display fallback, not dataset data
}

export interface MapEdge {
  id: string;            // route_id, e.g. BUS_MIRPUR10_FARMGATE / ROAD_*
  mode: TransitMode;
  from: string;          // hub id
  to: string;            // hub id
  closed: boolean;
  geometry?: LatLngTuple[];
}

export interface MapAlert {
  id: string;
  hubId: string;
  type: string;
  severity: string;
  status: string;        // pending | verified | downvoted | resolved | expired (| active)
  description: string;
  closedEdgeIds: string[];
}

export interface MapLeg {
  mode: TransitMode;
  fromHubId: string;
  toHubId: string;
  routeId?: string;
  geometry?: LatLngTuple[];
}

export interface MapRoute {
  id: string;
  legs: MapLeg[];
}

export interface MapLayers {
  hubs: boolean;
  transit: boolean;   // MRT-6 + bus reference lines from transit-data.json
  routes: boolean;    // selected / alternative route
  alerts: boolean;
  closed: boolean;
}

export interface UserFix { lat: number; lng: number; accuracy: number }

/** The contract from README + implementation plan §8. Names are frozen. */
export interface MapViewProps {
  hubs: MapHub[];
  routes: MapEdge[];
  alerts: MapAlert[];
  selectedRoute?: MapRoute | null;
  alternativeRoute?: MapRoute | null;
  closedRouteIds?: string[];
  highlightHubIds?: string[];
  theme: MapTheme;
  layers: MapLayers;

  onHubClick?: (hubId: string) => void;
  onAlertClick?: (alertId: string) => void;
  onMapClick?: (lat: number, lng: number) => void;
  onRouteClick?: (routeId: string) => void;

  /** Extras (optional, additive — do not break the frozen contract) */
  locateOnMount?: boolean;                 // default true (turn off on pages that don't need GPS)
  onLocationChange?: (fix: UserFix | null) => void;
  onUseAsOrigin?: (hubId: string) => void;   // "nearest hub" chip → search form
  labels?: Partial<MapLabels>;             // i18n strings injected by ConnectedMap
  className?: string;
}

export interface MapLabels {
  locate: string; locating: string; outside: string; denied: string;
  insecure: string; unavailable: string; tilesDown: string; goToMe: string;
  youAreHere: string; layers: string; sample: string; nearest: string; useAsFrom: string; closed: string;
  layerHubs: string; layerTransit: string; layerAlerts: string; layerClosed: string;
}
