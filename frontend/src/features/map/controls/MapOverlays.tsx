import { IconButton, Paper, Typography, ToggleButton, ToggleButtonGroup, Button } from '@mui/material';
import MyLocationIcon from '@mui/icons-material/MyLocation';
import LayersIcon from '@mui/icons-material/Layers';
import type { GeoStatus } from '../useGeolocation';
import type { MapLabels, MapLayers } from '../types';

export function LocateButton({ status, onClick, labels }: { status: GeoStatus; onClick: () => void; labels: MapLabels }) {
  const busy = status === 'locating';
  return (
    <IconButton className="dp-btn44" onClick={onClick} disabled={busy} aria-label={busy ? labels.locating : labels.locate} title={busy ? labels.locating : labels.locate}
      sx={{ width: 44, height: 44, bgcolor: 'background.paper', border: '1px solid', borderColor: 'divider', borderRadius: 1.5, '&:hover': { bgcolor: 'action.hover' } }}>
      <MyLocationIcon color={status === 'granted' ? 'primary' : 'inherit'} />
    </IconButton>
  );
}

export function LayerToggles({ layers, onChange, labels }: { layers: MapLayers; onChange: (l: MapLayers) => void; labels: MapLabels }) {
  const active = (Object.keys(layers) as (keyof MapLayers)[]).filter((k) => layers[k]);
  return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', p: 0.5, display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <LayersIcon fontSize="small" sx={{ ml: 0.5, color: 'text.secondary' }} titleAccess={labels.layers} />
      <ToggleButtonGroup size="small" value={active} onChange={(_, v: (keyof MapLayers)[]) =>
        onChange({ hubs: v.includes('hubs'), transit: v.includes('transit'), routes: v.includes('routes'), alerts: v.includes('alerts'), closed: v.includes('closed') })}>
        {([['hubs', labels.layerHubs], ['transit', labels.layerTransit], ['alerts', labels.layerAlerts], ['closed', labels.layerClosed]] as const).map(([k, text]) => (
          <ToggleButton key={k} value={k} aria-label={text} sx={{ px: 1, minHeight: 44, textTransform: 'none', fontSize: 12 }}>{text}</ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Paper>
  );
}

export function MapBanner({ text, action, onAction }: { text: string; action?: string; onAction?: () => void }) {
  return (
    <Paper elevation={0} role="status" sx={{ border: '1px solid', borderColor: 'divider', px: 1.5, py: 1, display: 'flex', alignItems: 'center', gap: 1, maxWidth: 520 }}>
      <Typography variant="caption" sx={{ flex: 1 }}>{text}</Typography>
      {action && onAction && <Button size="small" onClick={onAction} sx={{ minHeight: 36 }}>{action}</Button>}
    </Paper>
  );
}
