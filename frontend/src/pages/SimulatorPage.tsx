import React from 'react';
import { Container, Box } from '@mui/material';
import SimulatorPanel from '../features/simulator/SimulatorPanel';
import ConnectedMap from '../features/map/ConnectedMap';

const SimulatorPage: React.FC = () => (
  <Container maxWidth="md" sx={{ py: 3 }}>
    <Box sx={{
      display: 'grid',
      gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
      gap: 3,
    }}>
      <SimulatorPanel />
      <Box sx={{ borderRadius: 3, overflow: 'hidden', border: '1px solid', borderColor: 'divider', height: { xs: 360, md: 520 } }}>
        <ConnectedMap locate={false} />
      </Box>
    </Box>
  </Container>
);

export default SimulatorPage;
