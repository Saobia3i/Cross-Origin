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
import { searchTrips } from '../../api/endpoints';

const HUBS = [
  'Motijheel', 'Banasree', 'Malibagh', 'Shahbagh', 'Farmgate',
  'Science Lab', 'Dhanmondi', 'Mohammadpur', 'Mirpur 10', 'Agargaon',
  'Mohakhali', 'Badda', 'Uttara', 'Jatrabari', 'Kazipara',
];

const TripSearchForm: React.FC = () => {
  const { t } = useTranslation();
  const { origin, destination, budget, studentMode, avoidHubs, sortBy,
          setSearch, setResults, setSortBy } = useTripStore();
  const [loading, setLoading] = useState(false);

  const handleSearch = async () => {
    if (!origin || !destination) return;
    setLoading(true);
    try {
      const results = await searchTrips({
        origin, destination,
        budget: budget ?? undefined,
        student_mode: studentMode,
        avoid_hubs: avoidHubs,
        optimize: sortBy,
      });
      setResults(Array.isArray(results) ? results : []);
    } catch {
      setResults([]);
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
            slotProps={{ input: { ...params.InputProps, startAdornment: <MyLocationIcon sx={{ fontSize: 16, color: 'primary.main', mr: 0.5 }} /> } }}
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
        {(['cheapest', 'fastest', 'fewest'] as const).map((s) => (
          <Chip
            key={s} label={t(`trip.${s}`)} size="small"
            onClick={() => setSortBy(s)}
            variant={sortBy === s ? 'filled' : 'outlined'}
            color={sortBy === s ? 'primary' : 'default'}
            icon={<TrendingUpIcon sx={{ fontSize: '14px !important' }} />}
          />
        ))}
      </Box>

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
