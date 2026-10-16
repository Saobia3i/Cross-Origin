import React from 'react';
import { Box, Grid } from '@mui/material';
import TripSearchForm from '../features/trip-search/TripSearchForm';
import RouteResultsList from '../features/trip-search/RouteResultsList';
import RouteDetail from '../features/trip-search/RouteDetail';
import AlertsFeed from '../features/alerts/AlertsFeed';
import { useTripStore } from '../store/trip';

// Map placeholder — teammate will replace with real Leaflet MapView
const MapPlaceholder: React.FC = () => (
  <Box sx={{
    width: '100%', height: '100%', minHeight: 400,
    background: 'linear-gradient(135deg, #0A0F1C 0%, #0F2340 50%, #0A0F1C 100%)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    borderRadius: 0, position: 'relative', overflow: 'hidden',
  }}>
    {/* Grid pattern */}
    <Box sx={{
      position: 'absolute', inset: 0, opacity: 0.08,
      backgroundImage: 'linear-gradient(#22D3EE 1px, transparent 1px), linear-gradient(90deg, #22D3EE 1px, transparent 1px)',
      backgroundSize: '40px 40px',
    }} />
    <Box sx={{ textAlign: 'center', zIndex: 1 }}>
      <Box sx={{ fontSize: 64, mb: 1 }}>🗺️</Box>
      <Box sx={{ color: '#22D3EE', fontFamily: 'var(--font-mono)', fontSize: 14 }}>
        Map component (Leaflet + OSM)
      </Box>
      <Box sx={{ color: '#647080', fontSize: 12, mt: 0.5 }}>
        Teammate's MapView goes here
      </Box>
    </Box>
  </Box>
);

const HomePage: React.FC = () => {
  const { selectedRoute, results } = useTripStore();

  return (
    <Box sx={{ height: 'calc(100vh - 56px)', display: 'flex', overflow: 'hidden' }}>
      {/* Left panel */}
      <Box sx={{
        width: { xs: '100%', md: 380 },
        display: { xs: selectedRoute ? 'none' : 'flex', md: 'flex' },
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
      <Box sx={{ flex: 1, display: { xs: 'block', md: 'block' } }}>
        <MapPlaceholder />
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
