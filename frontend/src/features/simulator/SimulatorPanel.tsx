import React, { useState } from 'react';
import {
  Box, Typography, Paper, Button, MenuItem, Select, FormControl,
  InputLabel, Divider, Chip,
} from '@mui/material';
import ThunderstormIcon from '@mui/icons-material/Thunderstorm';
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar';
import BlockIcon from '@mui/icons-material/Block';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import { runSimulation, resetSimulation } from '../../api/endpoints';
import { FareText } from '../../components/FareText';

const HUBS = [
  'Motijheel', 'Banasree', 'Malibagh', 'Shahbagh', 'Farmgate',
  'Science Lab', 'Dhanmondi', 'Mohammadpur', 'Mirpur 10', 'Agargaon',
];

interface SimResult {
  affected: string[];
  before: { fare: number; duration: number };
  after:  { fare: number; duration: number };
  detour: string;
}

const SimulatorPanel: React.FC = () => {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<'heavy_rain' | 'rush_hour' | 'block_intersection' | null>(null);
  const [zone, setZone]         = useState('Shahbagh');
  const [result, setResult]     = useState<SimResult | null>(null);
  const [loading, setLoading]   = useState(false);

  const SCENARIOS = [
    { key: 'heavy_rain',        label: t('sim.heavyRain'),         Icon: ThunderstormIcon, color: '#2563EB' },
    { key: 'rush_hour',         label: t('sim.rushHour'),          Icon: DirectionsCarIcon, color: '#F59E0B' },
    { key: 'block_intersection',label: t('sim.blockIntersection'), Icon: BlockIcon,        color: '#E11D48' },
  ] as const;

  const handleRun = async () => {
    if (!selected) return;
    setLoading(true);
    try {
      const res = await runSimulation(selected, zone);
      setResult(res as SimResult);
    } catch {
      // Use mock result for demo
      setResult({
        affected: ['Farmgate–Shahbagh', 'Shahbagh–Motijheel'],
        before:   { fare: 120, duration: 35 },
        after:    { fare: 180, duration: 60 },
        detour:   'Via Science Lab → Motijheel (Bus R7)',
      });
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    try { await resetSimulation(); } catch {}
    setResult(null);
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
            <Typography variant="caption" fontWeight={600} sx={{ display: 'block', color: selected === key ? color : 'text.primary' }}>
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

      {/* Results */}
      {result && (
        <Box>
          {/* Affected segments */}
          <Typography variant="subtitle2" fontWeight={700} sx={{ fontFamily: 'var(--font-heading)', mb: 1 }}>
            {t('sim.affected')}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2.5 }}>
            {result.affected.map((seg, i) => (
              <Chip key={i} label={seg} size="small" sx={{ backgroundColor: '#FFE4E6', color: '#E11D48', fontWeight: 500 }} />
            ))}
          </Box>

          {/* Before / After */}
          <Box sx={{ display: 'flex', gap: 1.5, mb: 2.5 }}>
            {[
              { label: t('sim.before'), data: result.before, color: '#16A34A', bg: '#DCFCE7' },
              { label: t('sim.after'),  data: result.after,  color: '#E11D48', bg: '#FFE4E6' },
            ].map(({ label, data, color, bg }) => (
              <Paper key={label} elevation={0} sx={{ flex: 1, p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, backgroundColor: bg }}>
                <Typography variant="caption" fontWeight={700} sx={{ color, display: 'block', mb: 1 }}>{label}</Typography>
                <FareText amount={data.fare} size="lg" sx={{ color, display: 'block', mb: 0.5 }} />
                <Typography variant="body2" sx={{ fontFamily: 'var(--font-mono)', color }}>
                  {data.duration} min
                </Typography>
              </Paper>
            ))}
          </Box>

          {/* Detour */}
          <Paper elevation={0} sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, backgroundColor: 'background.default' }}>
            <Typography variant="caption" fontWeight={700} color="primary.main">{t('sim.detour')}</Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{result.detour}</Typography>
          </Paper>
        </Box>
      )}
    </Box>
  );
};

export default SimulatorPanel;
