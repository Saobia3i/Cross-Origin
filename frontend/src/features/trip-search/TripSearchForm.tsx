import React, { useState } from 'react';
import {
  Box, TextField, Button, Typography, Switch, FormControlLabel,
  Autocomplete, Chip, Paper,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import MyLocationIcon from '@mui/icons-material/MyLocation';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import { useTranslation } from 'react-i18next';
import { useTripStore } from '../../store/trip';
import { useQuery } from '@tanstack/react-query';
import { searchTrips, fetchNetwork } from '../../api/endpoints';

const TripSearchForm: React.FC = () => {
  const { t } = useTranslation();
  const { origin, destination, budget, studentMode, avoidHubs, sortBy,
          setSearch, setResults, setSortBy } = useTripStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Hub names come from the backend network (no hardcoded Dhaka data).
  const { data: network } = useQuery({ queryKey: ['network'], queryFn: fetchNetwork });
  const HUBS = (network?.hubs ?? []).map((h) => h.name);

  const handleSearch = async () => {
    if (!origin || !destination) return;
    setLoading(true);
    setError('');
    try {
      const data = await searchTrips({
        origin, destination,
        budget_bdt: budget ?? undefined,
        prefer_student_fare: studentMode,
        avoid_hubs: avoidHubs,
        optimize: sortBy,
      });
      setResults(data.routes ?? []);
    } catch (e) {
      setResults([]);
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5, display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 1 }}>
        <SearchIcon fontSize="small" sx={{ color: 'primary.main' }} />
        {t('trip.search')}
      </Typography>

      <Autocomplete
        freeSolo options={HUBS} value={origin}
        onInputChange={(_, v) => setSearch({ origin: v })}
        renderInput={(params) => (
          <TextField {...params} label={t('trip.from')} size="small"
            slotProps={{ ...params.slotProps, input: { ...params.slotProps.input, startAdornment: <MyLocationIcon sx={{ fontSize: 16, color: 'primary.main', mr: 0.5 }} /> } }}
          />
        )}
      />

      <Autocomplete
        freeSolo options={HUBS} value={destination}
        onInputChange={(_, v) => setSearch({ destination: v })}
        renderInput={(params) => (
          <TextField {...params} label={t('trip.to')} size="small" />
        )}
      />

      <Box sx={{ display: 'flex', gap: 1.5 }}>
        <TextField
          label={t('trip.budget')} size="small" type="number"
          value={budget ?? ''}
          onChange={(e) => setSearch({ budget: e.target.value ? Number(e.target.value) : null })}
          sx={{ flex: 1 }}
          slotProps={{ htmlInput: { min: 0 } }}
        />
        <FormControlLabel
          control={
            <Switch
              size="small" checked={studentMode}
              onChange={(e) => setSearch({ studentMode: e.target.checked })}
              color="primary"
            />
          }
          label={<Typography variant="caption" fontWeight={600}>{t('fare.student')}</Typography>}
          sx={{ m: 0, flexShrink: 0 }}
        />
      </Box>

      {/* Sort chips */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
        {(['cheapest', 'fastest', 'fewest_transfers'] as const).map((s) => (
          <Chip
            key={s} label={t(`trip.${s === 'fewest_transfers' ? 'fewest' : s}`)} size="small"
            onClick={() => setSortBy(s)}
            variant={sortBy === s ? 'filled' : 'outlined'}
            color={sortBy === s ? 'primary' : 'default'}
            icon={<TrendingUpIcon sx={{ fontSize: '14px !important' }} />}
          />
        ))}
      </Box>

      {error && <Typography color="error.main" variant="caption">{error}</Typography>}

      <Button
        variant="contained" fullWidth onClick={handleSearch} disabled={loading || !origin || !destination}
        startIcon={<SearchIcon />}
        sx={{ height: 44 }}
      >
        {loading ? t('trip.searching') : t('trip.search')}
      </Button>
    </Paper>
  );
};

export default TripSearchForm;
