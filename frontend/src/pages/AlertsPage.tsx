import React from 'react';
import { Box, Typography, Button, Container } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import AlertsFeed from '../features/alerts/AlertsFeed';
import { useAuthStore } from '../store/auth';

const AlertsPage: React.FC = () => {
  const { t } = useTranslation();
  const { user } = useAuthStore();

  return (
    <Container maxWidth="md" sx={{ py: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700 }}>
          🔔 {t('nav.alerts')}
        </Typography>
        {user && (
          <Button component={Link} to="/report" variant="contained" startIcon={<AddIcon />}>
            {t('alert.report')}
          </Button>
        )}
      </Box>
      <AlertsFeed />
    </Container>
  );
};

export default AlertsPage;
