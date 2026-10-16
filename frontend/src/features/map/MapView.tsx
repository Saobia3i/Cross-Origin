import { useEffect, useMemo, useState } from 'react';
import { MapContainer, ZoomControl, useMap, useMapEvents } from 'react-leaflet';
import { DHAKA_CENTER } from './constants';
import { inDhaka, nearestHub } from './geo';
import { useGeolocation } from './useGeolocation';
import TileLayerSwitch, { type TileHealth } from './layers/TileLayerSwitch';
import UserLocationLayer from './layers/UserLocationLayer';
import HubsLayer from './layers/HubsLayer';
import TransitOverlayLayer from './layers/TransitOverlayLayer';
import RouteLayer from './layers/RouteLayer';
import ClosedRoutesLayer from './layers/ClosedRoutesLayer';
import AlertsLayer from './layers/AlertsLayer';
import { LayerToggles, LocateButton, MapBanner } from './controls/MapOverlays';
import type { MapLabels, MapViewProps } from './types';
import './map.css';

export const DEFAULT_LABELS: MapLabels = {
  locate: 'My location', locating: 'Locating…', goToMe: 'Go to my location',
  outside: "You're outside Dhaka — showing the Dhaka network.",
  denied: 'Location permission denied — showing Dhaka. You can enable it in your browser settings.',
  insecure: 'Location needs HTTPS (or localhost).', unavailable: "Couldn't get your location — showing Dhaka.",
  tilesDown: 'Map tiles unavailable — showing schematic view.', youAreHere: 'You are here',
  layers: 'Layers', sample: 'Sample data (backend offline)', nearest: 'Nearest hub', useAsFrom: 'Use as From', closed: 'Closed',
  layerHubs: 'Hubs', layerTransit: 'Transit lines', layerAlerts: 'Alerts', layerClosed: 'Closed roads',
};

/** Leaflet only re-measures on window resize — panels/drawers change the container size without one. */
function ResizeWatcher() {
  const map = useMap();
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize({ animate: false }));
    ro.observe(map.getContainer());
    return () => ro.disconnect();
  }, [map]);
  return null;
}
function ClickRelay({ onMapClick }: { onMapClick?: (lat: number, lng: number) => void }) {
  useMapEvents({ click: (e) => onMapClick?.(e.latlng.lat, e.latlng.lng) });
  return null;
}

export default function MapView(props: MapViewProps) {
  const { hubs, routes, alerts, selectedRoute, alternativeRoute, closedRouteIds = [], highlightHubIds = [], theme, layers,
          onHubClick, onAlertClick, onMapClick, onRouteClick, onLocationChange, onUseAsOrigin, locateOnMount = true, className } = props;
  const L = { ...DEFAULT_LABELS, ...props.labels };

  const geo = useGeolocation(locateOnMount);
  const [centerNonce, setCenterNonce] = useState(0);
  const [tiles, setTiles] = useState<TileHealth>('carto');
  const [layerState, setLayerState] = useState(layers);
  useEffect(() => setLayerState(layers), [layers]);
  useEffect(() => { onLocationChange?.(geo.fix); }, [geo.fix, onLocationChange]);

  const hubById = useMemo(() => new Map(hubs.map((h) => [h.id, h])), [hubs]);
  const outside = !!geo.fix && !inDhaka(geo.fix.lat, geo.fix.lng);
  const near = geo.fix && !outside ? nearestHub(geo.fix.lat, geo.fix.lng, hubs) : null;
  const note =
    geo.status === 'denied' ? L.denied : geo.status === 'insecure' ? L.insecure :
    geo.status === 'unavailable' || geo.status === 'timeout' ? L.unavailable : outside ? L.outside : null;

  return (
    <div className={`dp-map-wrap ${tiles === 'down' ? 'dp-map-wrap--schematic' : ''} ${className ?? ''}`}>
      <MapContainer center={DHAKA_CENTER} zoom={12} minZoom={2} className="dp-map"
        zoomControl={false} fadeAnimation={false} markerZoomAnimation={false}>
        <TileLayerSwitch theme={theme} onHealth={setTiles} />
        <ResizeWatcher />
        <ClickRelay onMapClick={onMapClick} />
        {layerState.transit && <TransitOverlayLayer />}
        {layerState.closed && <ClosedRoutesLayer edges={routes} closedIds={closedRouteIds} hubs={hubById} label={L.closed} onRouteClick={onRouteClick} />}
        {layerState.routes && <RouteLayer selected={selectedRoute} alternative={alternativeRoute} hubs={hubById} theme={theme} />}
        {layerState.hubs && <HubsLayer hubs={hubs} highlight={highlightHubIds} theme={theme} onHubClick={onHubClick} />}
        {layerState.alerts && <AlertsLayer alerts={alerts} hubs={hubById} resolveHub={(id) => hubById.get(id) ?? hubs.find((h) => h.name === id)} onAlertClick={onAlertClick} />}
        <UserLocationLayer fix={geo.fix} centerNonce={centerNonce} label={L.youAreHere} />
        <ZoomControl position="bottomright" />
      </MapContainer>

      <div className="dp-overlay dp-overlay--tr">
        <LocateButton status={geo.status} labels={L} onClick={() => { void geo.locate(); setCenterNonce((n) => n + 1); }} />
        <LayerToggles layers={layerState} onChange={setLayerState} labels={L} />
      </div>
      {near && onUseAsOrigin && (
        <div className="dp-overlay dp-overlay--bl">
          <MapBanner text={`${L.nearest}: ${near.hub.name} · ${Math.round(near.meters)} m`} action={L.useAsFrom} onAction={() => onUseAsOrigin(near.hub.id)} />
        </div>
      )}
      <div className="dp-overlay dp-overlay--tl">
        {tiles === 'down' && <MapBanner text={L.tilesDown} />}
        {note && <MapBanner text={note} action={outside ? L.goToMe : undefined} onAction={() => setCenterNonce((n) => n + 1)} />}
      </div>
    </div>
  );
}
