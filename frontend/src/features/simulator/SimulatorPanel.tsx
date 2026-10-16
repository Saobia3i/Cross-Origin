import React, { useState } from 'react';
import {
  Box, Typography, Paper, Button, MenuItem, Select, FormControl,
  InputLabel, Chip, Autocomplete, TextField, Alert,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { useAuthStore } from '../../store/auth';
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
import { hubOptions } from '../map/hubs';

const NOTES: Record<string, string> = {
  NO_JOURNEY: 'Enter an origin and a destination above to compare the journey.',
};
const mins = (n?: number | null) => (n == null ? '—' : `${Math.round(n)} min`);

const SimulatorPanel: React.FC = () => {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<ScenarioType | null>(null);
  const [zone, setZone]         = useState('');
  const [result, setResult]     = useState<SimulationResult | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');
  const { origin: tripOrigin, destination: tripDestination, studentMode, setSearch } = useTripStore();
  const user = useAuthStore((s) => s.user);
  // The journey to compare. Prefilled from the last trip search, but editable right here.
  const [origin, setOrigin]           = useState(tripOrigin);
  const [destination, setDestination] = useState(tripDestination);
  const needsZone = selected === 'blocked_intersection';
  const { data: network } = useQuery({ queryKey: ['network'], queryFn: fetchNetwork });
  const HUB_OPTIONS = hubOptions(network?.hubs);
  const HUBS = HUB_OPTIONS.map((o) => o.name);

  const SCENARIOS = [
    { key: 'heavy_rain',           label: t('sim.heavyRain'),         Icon: ThunderstormIcon, color: '#2563EB' },
    { key: 'peak_rush_hour',       label: t('sim.rushHour'),          Icon: DirectionsCarIcon, color: '#F59E0B' },
    { key: 'blocked_intersection', label: t('sim.blockIntersection'), Icon: BlockIcon,        color: '#E11D48' },
  ] as const;

  // Simulations are per session and need a verified login (backend: verified_user).
  const handleRun = async () => {
    if (!selected) return;
    if (needsZone && !zone) { setError('Pick the intersection (zone) to block.'); return; }
    if (!origin || !destination) { setError('Enter an origin and a destination to compare the journey.'); return; }
    setSearch({ origin, destination });
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
    setZone('');
  };

  return (
    <Box>
      <Typography variant="h5" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, mb: 2 }}>
        {t('sim.title')}
      </Typography>

      {/* Scenario buttons */}
      {!user && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Running a scenario needs a verified account.{' '}
          <RouterLink to="/login">Log in</RouterLink>
        </Alert>
      )}
      {user && !user.is_verified && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Your account is not verified yet, so simulations are disabled. Ask an admin to verify it.
        </Alert>
      )}

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

      {/* Journey to compare */}
      <Box sx={{ display: 'flex', gap: 1.5, mb: 2, flexDirection: { xs: 'column', sm: 'row' } }}>
        <Autocomplete freeSolo fullWidth size="small" options={HUB_OPTIONS} groupBy={(o) => o.group} getOptionLabel={(o) => (typeof o === 'string' ? o : o.name)} value={origin}
          onInputChange={(_, v) => setOrigin(v)}
          renderInput={(params) => <TextField {...params} label={t('trip.from')} />} />
        <Autocomplete freeSolo fullWidth size="small" options={HUB_OPTIONS} groupBy={(o) => o.group} getOptionLabel={(o) => (typeof o === 'string' ? o : o.name)} value={destination}
          onInputChange={(_, v) => setDestination(v)}
          renderInput={(params) => <TextField {...params} label={t('trip.to')} />} />
      </Box>

      {/* Zone selector */}
      <FormControl size="small" fullWidth sx={{ mb: 2 }}>
        <InputLabel>{t('sim.selectZone')}{needsZone ? ' *' : ' (optional)'}</InputLabel>
        <Select value={zone} label={`${t('sim.selectZone')}${needsZone ? ' *' : ' (optional)'}`} onChange={(e) => setZone(e.target.value)}>
          <MenuItem value=""><em>None</em></MenuItem>
          {HUBS.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
        </Select>
      </FormControl>

      <Box sx={{ display: 'flex', gap: 1.5, mb: 3 }}>
        <Button
          variant="contained" onClick={handleRun} disabled={!selected || loading || !user || !user.is_verified}
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
                    <Typography variant="body2" sx={{ fontFamily: 'var(--font-mono)', color }}>{mins(dur)}</Typography>
                  </Paper>
                ))}
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {(c.note && NOTES[c.note]) || c.note || 'No before/after comparison is available for this journey.'}
              </Typography>
            )}
            <Paper elevation={0} sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, backgroundColor: 'background.default' }}>
              <Typography variant="caption" color="primary.main" sx={{ fontWeight: 700 }}>{t('sim.detour')}</Typography>
              <Typography variant="body2" sx={{ mt: 0.5 }}>
                {c.rerouted
                  ? `Rerouted: ${(c.original_route_ids as string[] | undefined)?.join(' + ') ?? 'original route'} → ${(c.new_route_ids as string[] | undefined)?.join(' + ') ?? 'detour'}`
                  : result.broken_segments.length === 0
                    ? 'This scenario does not break your journey. Try another zone or scenario.'
                    : 'No reroute found.'}
                {c.note && !NOTES[c.note] ? ` (${c.note})` : ''}
              </Typography>
              {c.available && c.added_time_min != null && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {c.added_time_min >= 0 ? '+' : ''}{Math.round(c.added_time_min)} min vs normal
                </Typography>
              )}
            </Paper>
          </Box>
        );
      })()}
    </Box>
  );
};

export default SimulatorPanel;
