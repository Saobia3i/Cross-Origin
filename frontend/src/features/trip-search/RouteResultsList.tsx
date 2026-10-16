import React from 'react';
import { Box, Typography, Divider, Chip, Paper } from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import WarningIcon from '@mui/icons-material/Warning';
import { useTranslation } from 'react-i18next';
import { useTripStore } from '../../store/trip';
import { ModePill } from '../../components/ModePill';
import { FareText } from '../../components/FareText';
import { GroundingBadge, EmptyState } from '../../components/Feedback';
import type { RouteOption } from '../../api/schemas';

const RouteCard: React.FC<{ route: RouteOption; selected: boolean; onClick: () => void }> = ({ route, selected, onClick }) => {
  const { t } = useTranslation();
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
            <ModePill mode={leg.mode as any} />
            {i < route.legs.length - 1 && <ArrowForwardIcon sx={{ fontSize: 14, color: 'text.disabled' }} />}
          </React.Fragment>
        ))}
      </Box>

      {/* Stats row */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Box>
          <FareText amount={route.total_fare} size="lg" />
          {route.within_budget && (
            <Chip label={t('trip.withinBudget')} size="small" sx={{ ml: 1, backgroundColor: '#DCFCE7', color: '#16A34A', height: 20, fontSize: '0.65rem' }} />
          )}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ fontFamily: 'var(--font-mono)' }}>
          {t('trip.duration', { min: route.total_duration })}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {route.transfers === 0 ? 'Direct' : t('trip.transfers', { n: route.transfers, count: route.transfers })}
        </Typography>

        <Box sx={{ ml: 'auto', display: 'flex', gap: 0.75, alignItems: 'center' }}>
          {route.disrupted && <WarningIcon sx={{ fontSize: 16, color: 'warning.main' }} />}
          <GroundingBadge ok={route.grounding_ok} />
        </Box>
      </Box>
    </Paper>
  );
};

const RouteResultsList: React.FC = () => {
  const { t } = useTranslation();
  const { results, selectedRoute, selectRoute } = useTripStore();

  if (results.length === 0) return null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Typography variant="subtitle2" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 600, color: 'text.secondary' }}>
        {t('trip.results')} ({results.length})
      </Typography>
      {results.map((route) => (
        <RouteCard
          key={route.id}
          route={route}
          selected={selectedRoute?.id === route.id}
          onClick={() => selectRoute(route)}
        />
      ))}
    </Box>
  );
};

export default RouteResultsList;
