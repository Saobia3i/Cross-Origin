import React from 'react';
import { Box } from '@mui/material';
import TripSearchForm from '../features/trip-search/TripSearchForm';
import RouteResultsList from '../features/trip-search/RouteResultsList';
import RouteDetail from '../features/trip-search/RouteDetail';
import AlertsFeed from '../features/alerts/AlertsFeed';
import { useTripStore } from '../store/trip';
import ConnectedMap from '../features/map/ConnectedMap';

const HomePage: React.FC = () => {
  const { selectedRoute, results } = useTripStore();

  return (
    <Box sx={{
      height: { xs: 'calc(100dvh - 56px - 60px)', md: 'calc(100dvh - 56px)' },
      display: 'flex', flexDirection: { xs: 'column', md: 'row' }, overflow: 'hidden',
    }}>
      {/* Left panel */}
      <Box sx={{
        width: { xs: '100%', md: 380 },
        order: { xs: 2, md: 0 },
        flex: { xs: '0 0 auto', md: '0 0 380px' },
        maxHeight: { xs: selectedRoute ? '38%' : '46%', md: 'none' },
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'background.paper',
        overflow: 'hidden',
      }}>
        <Box sx={{ p: 2, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TripSearchForm />
          {results.length > 0 && <RouteResultsList />}
          {selectedRoute && <RouteDetail />}
        </Box>
      </Box>

      {/* Center map */}
      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, order: { xs: 1, md: 0 }, position: 'relative' }}>
        <ConnectedMap />
      </Box>

      {/* Right panel — desktop alerts strip */}
      <Box sx={{
        width: 300, display: { xs: 'none', lg: 'flex' },
        flexDirection: 'column',
        borderLeft: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'background.paper',
        overflow: 'hidden',
      }}>
        <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Box sx={{ fontSize: '0.875rem', fontWeight: 700, fontFamily: 'var(--font-heading)' }}>
            🔔 Live Alerts
          </Box>
        </Box>
        <Box sx={{ p: 2, overflowY: 'auto', flex: 1 }}>
          <AlertsFeed />
        </Box>
      </Box>
    </Box>
  );
};

export default HomePage;
