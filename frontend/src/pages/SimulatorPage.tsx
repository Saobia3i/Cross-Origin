import React from 'react';
import { Container, Box } from '@mui/material';
import SimulatorPanel from '../features/simulator/SimulatorPanel';

const SimulatorPage: React.FC = () => (
  <Container maxWidth="md" sx={{ py: 3 }}>
    <Box sx={{
      display: 'grid',
      gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
      gap: 3,
    }}>
      <SimulatorPanel />
      {/* Map snapshot — teammate fills this */}
      <Box sx={{
        background: 'linear-gradient(135deg,#0A0F1C,#0F2340)',
        borderRadius: 3, minHeight: 400,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px dashed', borderColor: 'divider',
      }}>
        <Box sx={{ textAlign: 'center', color: '#647080', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
          Map snapshot<br />(teammate)
        </Box>
      </Box>
    </Box>
  </Container>
);

export default SimulatorPage;
