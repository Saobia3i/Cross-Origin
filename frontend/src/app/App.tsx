import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ThemeProvider } from '@mui/material/styles';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';

import i18n from './i18n';
import { lightTheme, darkTheme } from './theme';
import { useUIStore } from '../store/ui';
import Layout from '../components/Layout';
import { useEventPolling } from '../hooks/useEventPolling';

// Pages
import HomePage       from '../pages/HomePage';
import AlertsPage     from '../pages/AlertsPage';
import SimulatorPage  from '../pages/SimulatorPage';
import NetworkPage    from '../pages/NetworkPage';
import AdminPage      from '../pages/AdminPage';
import { LoginPage, RegisterPage } from '../pages/AuthPages';
import ReportForm     from '../features/reports/ReportForm';

// Styles
import '../styles/global.css';
import 'leaflet/dist/leaflet.css';

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

const AppInner: React.FC = () => {
  const { theme } = useUIStore();
  const muiTheme  = theme === 'dark' ? darkTheme : lightTheme;
  useEventPolling();

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <ThemeProvider theme={muiTheme}>
      <BrowserRouter>
        <Routes>
          {/* Auth pages — no layout */}
          <Route path="/login"    element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* Main app — with layout */}
          <Route element={<Layout><React.Fragment /></Layout>} path="/__layout_test" />

          <Route path="/" element={<Layout><HomePage /></Layout>} />
          <Route path="/alerts"    element={<Layout><AlertsPage /></Layout>} />
          <Route path="/simulator" element={<Layout><SimulatorPage /></Layout>} />
          <Route path="/network"   element={<Layout><NetworkPage /></Layout>} />
          <Route path="/report"    element={<Layout><div style={{maxWidth:520,margin:'24px auto'}}><ReportForm /></div></Layout>} />
          <Route path="/admin"     element={<Layout><AdminPage /></Layout>} />
          <Route path="*"          element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
};

const App: React.FC = () => (
  <I18nextProvider i18n={i18n}>
    <QueryClientProvider client={qc}>
      <AppInner />
    </QueryClientProvider>
  </I18nextProvider>
);

export default App;
