import React, { useState } from 'react';
import {
  Box, Typography, Paper, Button, MenuItem, Select, FormControl,
  InputLabel, Chip,
} from '@mui/material';
import ThunderstormIcon from '@mui/icons-material/Thunderstorm';
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar';
import BlockIcon from '@mui/icons-material/Block';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import { runSimulation, resetSimulation } from '../../api/endpoints';
import { FareText } from '../../components/FareText';
import { fetchNetwork } from '../../api/endpoints';
import { useQuery } from '@tanstack/react-query';
import { useTripStore } from '../../store/trip';
import type { ScenarioType, SimulationResult } from '../../api/schemas';
import { useMapOverlay } from '../map/mapOverlay';

const SimulatorPanel: React.FC = () => {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<ScenarioType | null>(null);
  const [zone, setZone]         = useState('');
  const [result, setResult]     = useState<SimulationResult | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');
  const { origin, destination, studentMode } = useTripStore();
  const { data: network } = useQuery({ queryKey: ['network'], queryFn: fetchNetwork });
  const HUBS = (network?.hubs ?? []).map((h) => h.name);

  const SCENARIOS = [
    { key: 'heavy_rain',           label: t('sim.heavyRain'),         Icon: ThunderstormIcon, color: '#2563EB' },
    { key: 'peak_rush_hour',       label: t('sim.rushHour'),          Icon: DirectionsCarIcon, color: '#F59E0B' },
    { key: 'blocked_intersection', label: t('sim.blockIntersection'), Icon: BlockIcon,        color: '#E11D48' },
  ] as const;

  // Simulations are per session and need a verified login (backend: verified_user).
  const handleRun = async () => {
    if (!selected) return;
    setLoading(true);
    setError('');
    try {
      const res = await runSimulation(selected, {
        zone_hub: zone || undefined,
        origin: origin || undefined,
        destination: destination || undefined,
        prefer_student_fare: studentMode,
      });
      useMapOverlay.getState().setSimulation(res);   // map draws closed roads + detour
      setResult(res);
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    try { await resetSimulation(); } catch { /* nothing active */ }
    useMapOverlay.getState().clear();
    setResult(null);
    setError('');
    setSelected(null);
  };

  return (
    <Box>
      <Typography variant="h5" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, mb: 2 }}>
        {t('sim.title')}
      </Typography>

      {/* Scenario buttons */}
      <Box sx={{ display: 'flex', gap: 1.5, mb: 2.5, flexWrap: 'wrap' }}>
        {SCENARIOS.map(({ key, label, Icon, color }) => (
          <Paper
            key={key} elevation={0}
            onClick={() => setSelected(key)}
            sx={{
              flex: 1, minWidth: 120, p: 2, border: '1px solid',
              borderColor: selected === key ? color : 'divider',
              borderRadius: 2, cursor: 'pointer', textAlign: 'center',
              backgroundColor: selected === key ? `${color}11` : 'background.paper',
              '&:hover': { borderColor: color },
              transition: 'border-color 120ms ease, background-color 120ms ease',
            }}
          >
            <Icon sx={{ color, fontSize: 32, mb: 1 }} />
            <Typography variant="caption" sx={{ fontWeight: 600, display: 'block', color: selected === key ? color : 'text.primary' }}>
              {label}
            </Typography>
          </Paper>
        ))}
      </Box>

      {/* Zone selector */}
      <FormControl size="small" fullWidth sx={{ mb: 2 }}>
        <InputLabel>{t('sim.selectZone')}</InputLabel>
        <Select value={zone} label={t('sim.selectZone')} onChange={(e) => setZone(e.target.value)}>
          {HUBS.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
        </Select>
      </FormControl>

      <Box sx={{ display: 'flex', gap: 1.5, mb: 3 }}>
        <Button
          variant="contained" onClick={handleRun} disabled={!selected || loading}
          sx={{ flex: 1, height: 44 }}
        >
          {loading ? 'Running…' : 'Run Scenario'}
        </Button>
        <Button variant="outlined" startIcon={<RefreshIcon />} onClick={handleReset} sx={{ height: 44 }}>
          {t('sim.reset')}
        </Button>
      </Box>

      {error && <Typography color="error.main" variant="caption" sx={{ display: 'block', mb: 2 }}>{error}</Typography>}

      {/* Results (from before_after_comparison) */}
      {result && (() => {
        const c = result.before_after_comparison;
        return (
          <Box>
            {result.broken_segments.length > 0 && (
              <>
                <Typography variant="subtitle2" sx={{ fontWeight: 700, fontFamily: 'var(--font-heading)', mb: 1 }}>
                  {t('sim.affected')}
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2.5 }}>
                  {result.broken_segments.map((seg, i) => (
                    <Chip key={i} label={typeof seg === 'string' ? seg : JSON.stringify(seg)} size="small" sx={{ backgroundColor: '#FFE4E6', color: '#E11D48', fontWeight: 500 }} />
                  ))}
                </Box>
              </>
            )}
            {c.available ? (
              <Box sx={{ display: 'flex', gap: 1.5, mb: 2.5 }}>
                {[
                  { label: t('sim.before'), min: c.original_fare_min, max: c.original_fare_max, dur: c.original_duration_min, color: '#16A34A', bg: '#DCFCE7' },
                  { label: t('sim.after'),  min: c.new_fare_min,      max: c.new_fare_max,      dur: c.new_duration_min,      color: '#E11D48', bg: '#FFE4E6' },
                ].map(({ label, min, max, dur, color, bg }) => (
                  <Paper key={label} elevation={0} sx={{ flex: 1, p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, backgroundColor: bg }}>
                    <Typography variant="caption" sx={{ fontWeight: 700, color, display: 'block', mb: 1 }}>{label}</Typography>
                    <FareText min={min} max={max} size="lg" sx={{ color, display: 'block', mb: 0.5 }} />
                    <Typography variant="body2" sx={{ fontFamily: 'var(--font-mono)', color }}>{dur} min</Typography>
                  </Paper>
                ))}
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Search a trip first to compare before/after for your journey.
              </Typography>
            )}
            <Paper elevation={0} sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, backgroundColor: 'background.default' }}>
              <Typography variant="caption" sx={{ fontWeight: 700, color: "primary.main" }}>{t('sim.detour')}</Typography>
              <Typography variant="body2" sx={{ mt: 0.5 }}>
                {c.rerouted ? 'Rerouted' : 'No reroute needed'}{c.note ? ' - ' + c.note : ''}
              </Typography>
            </Paper>
          </Box>
        );
      })()}
    </Box>
  );
};

export default SimulatorPanel;
