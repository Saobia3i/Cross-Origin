import React from 'react';
import {
  Box, Typography, Paper, Divider, Switch, FormControlLabel,
  Table, TableBody, TableCell, TableRow, Chip,
} from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import InfoIcon from '@mui/icons-material/Info';
import { useTranslation } from 'react-i18next';
import { useTripStore } from '../../store/trip';
import { ModePill } from '../../components/ModePill';
import { FareText } from '../../components/FareText';

const RouteDetail: React.FC = () => {
  const { t } = useTranslation();
  const { selectedRoute, studentMode, setSearch } = useTripStore();

  if (!selectedRoute) return null;

  return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700 }}>
          {t('trip.legs')}
        </Typography>
        <FormControlLabel
          control={
            <Switch size="small" checked={studentMode}
              onChange={(e) => setSearch({ studentMode: e.target.checked })} color="primary" />
          }
          label={<Typography variant="caption" fontWeight={600}>{t('fare.student')}</Typography>}
          sx={{ m: 0 }}
        />
      </Box>

      {/* Leg timeline */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {selectedRoute.legs.map((leg, i) => {
          const fare = studentMode && leg.student_fare != null ? leg.student_fare : leg.fare;
          return (
            <Box key={i}>
              <Box sx={{ display: 'flex', gap: 2, py: 1.5 }}>
                {/* Timeline dot */}
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20, flexShrink: 0 }}>
                  <Box sx={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: 'primary.main', mt: 0.5 }} />
                  {i < selectedRoute.legs.length - 1 && (
                    <Box sx={{ width: 2, flex: 1, backgroundColor: 'divider', my: 0.25 }} />
                  )}
                </Box>

                {/* Leg content */}
                <Box sx={{ flex: 1, pb: 1 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                    <ModePill mode={leg.mode as any} />
                    <Typography variant="body2" fontWeight={600}>{leg.from}</Typography>
                    <ArrowForwardIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
                    <Typography variant="body2" fontWeight={600}>{leg.to}</Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
                    <FareText amount={fare} size="sm" />
                    <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'var(--font-mono)' }}>
                      {t('trip.duration', { min: leg.duration_min })}
                    </Typography>
                    {studentMode && leg.student_fare != null && (
                      <Chip label={t('fare.student')} size="small" sx={{ height: 18, fontSize: '0.6rem', backgroundColor: '#FEF3C7', color: '#B45309' }} />
                    )}
                  </Box>
                  {leg.boarding && (
                    <Box sx={{ mt: 0.75, p: 1, backgroundColor: 'background.default', borderRadius: 1, display: 'flex', gap: 0.5 }}>
                      <InfoIcon sx={{ fontSize: 14, color: 'info.main', mt: '1px', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">{leg.boarding}</Typography>
                    </Box>
                  )}
                </Box>
              </Box>
              {i < selectedRoute.legs.length - 1 && <Divider />}
            </Box>
          );
        })}
      </Box>

      {/* Fare summary */}
      <Divider sx={{ my: 2 }} />
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography fontWeight={600}>{t('fare.total')}</Typography>
        <FareText amount={selectedRoute.total_fare} size="lg" sx={{ color: 'primary.main' }} />
      </Box>
    </Paper>
  );
};

export default RouteDetail;
