import { useMemo, useState } from 'react';
import { Box, Button, IconButton, Paper, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { fetchReports, fetchNetwork } from '../../api/endpoints';
import { useTripStore } from '../../store/trip';
import { useUIStore } from '../../store/ui';
import MapView from './MapView';
import { useMapOverlay } from './mapOverlay';
import { makeHubIndex, toMapAlerts, toMapEdges, toMapHubs, toMapRoute, toMapSimulation } from './adapters';
import { MRT_STATIONS } from './geo';
import type { MapLabels, MapLayers } from './types';

// Real reference hubs from transit-data.json (MRT-6 stations) — used only until /api/transit/network answers.
const REFERENCE_NETWORK = {
  hubs: MRT_STATIONS.map((s) => ({ hub_id: s.id, name: s.name, lat: s.lat, lng: s.lng, type: 'interchange' })),
  routes: [],
};

const LAYERS: MapLayers = { hubs: true, transit: true, routes: true, alerts: true, closed: true };

/**
 * Wires stores/queries → MapView.
 * Same query keys as AlertsFeed / NetworkPage ⇒ shared cache, no extra requests.
 * `locate={false}` for pages that don't need GPS (e.g. /simulator).
 */
export default function ConnectedMap({ locate = true }: { locate?: boolean }) {
  const { t } = useTranslation();
  const theme = useUIStore((s) => s.theme);
  const selected = useTripStore((s) => s.selectedRoute);
  const { setSearch, avoidHubs } = useTripStore();
  const simulation = useMapOverlay((s) => s.simulation);
  const [activeHub, setActiveHub] = useState<string | null>(null);

  const net = useQuery({ queryKey: ['network'], queryFn: fetchNetwork });
  const al = useQuery({ queryKey: ['alerts'], queryFn: () => fetchReports({ limit: 100 }), refetchInterval: 12_000 });

  const hubs = useMemo(() => toMapHubs(net.data ?? REFERENCE_NETWORK), [net.data]);
  const edges = useMemo(() => toMapEdges(net.data ?? REFERENCE_NETWORK), [net.data]);
  const alerts = useMemo(() => toMapAlerts(al.data ?? []), [al.data]);
  const idx = useMemo(() => makeHubIndex(hubs), [hubs]);
  const picked = useMemo(() => toMapRoute(selected, idx), [selected, idx]);
  const sim = useMemo(() => toMapSimulation(simulation, idx), [simulation, idx]);

  // Before/after: user's route solid + detour dotted. No route chosen yet ⇒ show the detour solid.
  const selectedRoute = picked ?? sim.alternativeRoute;
  const alternativeRoute = picked ? sim.alternativeRoute : null;
  const ends = selectedRoute ? [selectedRoute.legs[0].fromHubId, selectedRoute.legs[selectedRoute.legs.length - 1].toHubId] : [];
  const hub = activeHub ? idx.get(activeHub) : undefined;

  const labels: MapLabels = {
    locate: t('map.locate'), locating: t('map.locating'), goToMe: t('map.goToMe'), outside: t('map.outside'),
    denied: t('map.denied'), insecure: t('map.insecure'), unavailable: t('map.unavailable'), tilesDown: t('map.tilesDown'),
    youAreHere: t('map.youAreHere'), layers: t('map.layers'), sample: t('map.sample'), nearest: t('map.nearest'),
    useAsFrom: t('map.useAsFrom'), closed: t('map.closed'),
    layerHubs: t('map.layerHubs'), layerTransit: t('map.layerTransit'), layerAlerts: t('map.layerAlerts'), layerClosed: t('map.layerClosed'),
  };

  return (
    <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
      <MapView
        hubs={hubs} routes={edges} alerts={alerts} theme={theme} layers={LAYERS}
        selectedRoute={selectedRoute} alternativeRoute={alternativeRoute}
        closedRouteIds={sim.closedRouteIds} highlightHubIds={ends}
        locateOnMount={locate} labels={labels}
        onHubClick={setActiveHub}
        onUseAsOrigin={(id) => { const h = idx.get(id); if (h) setSearch({ origin: h.name }); }}
      />


      {hub && (
        <Paper elevation={0} sx={{ position: 'absolute', zIndex: 1000, left: '50%', transform: 'translateX(-50%)', bottom: 56, border: '1px solid', borderColor: 'divider', p: 1.5, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', maxWidth: 'calc(100% - 24px)' }}>
          <Typography variant="subtitle2" sx={{ mr: 1 }}>{hub.name}</Typography>
          <Button size="small" variant="contained" sx={{ minHeight: 44 }} onClick={() => { setSearch({ origin: hub.name }); setActiveHub(null); }}>{t('trip.from')}</Button>
          <Button size="small" variant="contained" sx={{ minHeight: 44 }} onClick={() => { setSearch({ destination: hub.name }); setActiveHub(null); }}>{t('trip.to')}</Button>
          <Button size="small" variant="outlined" sx={{ minHeight: 44 }} onClick={() => { setSearch({ avoidHubs: [...new Set([...avoidHubs, hub.id])] }); setActiveHub(null); }}>{t('trip.avoid')}</Button>
          <IconButton aria-label={t('common.close')} onClick={() => setActiveHub(null)} sx={{ width: 44, height: 44 }}><CloseIcon fontSize="small" /></IconButton>
        </Paper>
      )}
    </Box>
  );
}
