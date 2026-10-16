import React from 'react';
import { Box, Typography, Chip, Paper } from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import WarningIcon from '@mui/icons-material/Warning';
import { useTranslation } from 'react-i18next';
import { useTripStore } from '../../store/trip';
import { ModePill } from '../../components/ModePill';
import { FareText } from '../../components/FareText';
import { GroundingBadge } from '../../components/Feedback';
import type { RouteOption } from '../../api/schemas';

const RouteCard: React.FC<{ route: RouteOption; selected: boolean; onClick: () => void }> = ({ route, selected, onClick }) => {
  const { t } = useTranslation();
  const studentMode = useTripStore((s) => s.studentMode);
  return (
    <Paper
      elevation={0}
      onClick={onClick}
      sx={{
        border: '1px solid', borderRadius: 2, p: 2, cursor: 'pointer',
        borderColor: selected ? 'primary.main' : 'divider',
        backgroundColor: selected ? 'primary.light' : 'background.paper',
        '&:hover': { borderColor: 'primary.main' },
        transition: 'border-color 120ms ease, background-color 120ms ease',
      }}
    >
      {/* Mode pills row */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mb: 1.5 }}>
        {route.legs.map((leg, i) => (
          <React.Fragment key={i}>
            <ModePill mode={leg.mode} />
            {i < route.legs.length - 1 && <ArrowForwardIcon sx={{ fontSize: 14, color: 'text.disabled' }} />}
          </React.Fragment>
        ))}
      </Box>

      {/* Stats row */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Box>
          <FareText
            min={studentMode ? route.total_student_fare_min_bdt : route.total_regular_fare_min_bdt}
            max={studentMode ? route.total_student_fare_max_bdt : route.total_regular_fare_max_bdt}
            size="lg"
          />
          <Chip label={route.rank_label} size="small" variant="outlined" sx={{ ml: 1, height: 20, fontSize: '0.65rem' }} />
          {route.within_budget && (
            <Chip label={t('trip.withinBudget')} size="small" sx={{ ml: 1, backgroundColor: '#DCFCE7', color: '#16A34A', height: 20, fontSize: '0.65rem' }} />
          )}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ fontFamily: 'var(--font-mono)' }}>
          {route.total_duration_min == null ? 'Time unavailable' : t('trip.duration', { min: route.total_duration_min })}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {route.transfers === 0 ? 'Direct' : t('trip.transfers', { n: route.transfers, count: route.transfers })}
        </Typography>

        <Box sx={{ ml: 'auto', display: 'flex', gap: 0.75, alignItems: 'center' }}>
          {route.disruption_risk !== 'low' && <WarningIcon sx={{ fontSize: 16, color: 'warning.main' }} />}
          <GroundingBadge ok={route.grounding?.valid ?? false} />
        </Box>
      </Box>
    </Paper>
  );
};

const RouteResultsList: React.FC = () => {
  const { t } = useTranslation();
  const { results, selectedRoute, selectRoute, source, setSource, searchResults, aiResults } = useTripStore();

  if (results.length === 0) return null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Typography variant="subtitle2" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 600, color: 'text.secondary' }}>
        {source === 'ai' ? 'AI route options' : t('trip.results')} ({results.length})
      </Typography>
      {searchResults.length > 0 && aiResults.length > 0 && (
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Chip size="small" label="Search results" onClick={() => setSource('search')} color={source === 'search' ? 'primary' : 'default'} variant={source === 'search' ? 'filled' : 'outlined'} />
          <Chip size="small" label="AI options" onClick={() => setSource('ai')} color={source === 'ai' ? 'primary' : 'default'} variant={source === 'ai' ? 'filled' : 'outlined'} />
        </Box>
      )}
      {results.map((route) => (
        <RouteCard
          key={route.route_option_id}
          route={route}
          selected={selectedRoute?.route_option_id === route.route_option_id}
          onClick={() => selectRoute(route)}
        />
      ))}
    </Box>
  );
};

export default RouteResultsList;
