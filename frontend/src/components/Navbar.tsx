import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar, Toolbar, Box, IconButton, Button, Tooltip, Typography,
} from '@mui/material';
import {
  Map, Search, NotificationsNone, Chat, GridView,
  DarkMode, LightMode, AdminPanelSettings,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../store/ui';
import { useAuthStore } from '../store/auth';
import { useChatStore } from '../store/chat';

const NAV = [
  { key: 'plan',     path: '/',          Icon: Map },
  { key: 'alerts',   path: '/alerts',    Icon: NotificationsNone },
  { key: 'simulate', path: '/simulator', Icon: Search },
  { key: 'network',  path: '/network',   Icon: GridView },
];

export const Navbar: React.FC = () => {
  const { t }         = useTranslation();
  const { theme, lang, toggleTheme, setLang } = useUIStore();
  const { user, clearAuth } = useAuthStore();
  const { toggleChat } = useChatStore();
  const location      = useLocation();
  const navigate      = useNavigate();

  const isDark = theme === 'dark';

  return (
    <AppBar
      position="fixed"
      elevation={0}
      sx={{
        backgroundColor: 'background.paper',
        borderBottom:    '1px solid',
        borderColor:     'divider',
        color:           'text.primary',
        zIndex:          1300,
      }}
    >
      <Toolbar sx={{ height: 56, minHeight: '56px !important', px: { xs: 2, md: 3 }, gap: 1 }}>
        {/* Logo */}
        <Box component={Link} to="/" sx={{ display: 'flex', alignItems: 'center', gap: 1, textDecoration: 'none', mr: 2 }}>
          <Box sx={{
            width: 32, height: 32, borderRadius: 1.5,
            background: 'linear-gradient(135deg, #0891B2 0%, #155E75 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18,
          }}>🗺</Box>
          <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, color: 'text.primary', display: { xs: 'none', sm: 'block' } }}>
            DhakaPath
          </Typography>
        </Box>

        {/* Desktop Nav */}
        <Box sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'center', gap: 0.5, flex: 1 }}>
          {NAV.map(({ key, path, Icon }) => {
            const active = location.pathname === path;
            return (
              <Button
                key={key}
                component={Link}
                to={path}
                startIcon={<Icon sx={{ fontSize: 18 }} />}
                sx={{
                  color:           active ? 'primary.main' : 'text.secondary',
                  backgroundColor: active ? 'primary.light' : 'transparent',
                  fontWeight:      active ? 600 : 400,
                  '&:hover':       { backgroundColor: active ? 'primary.light' : 'action.hover', color: 'text.primary' },
                  px: 1.5, py: 0.75,
                }}
              >
                {t(`nav.${key}`)}
              </Button>
            );
          })}
        </Box>

        <Box sx={{ flex: 1, display: { md: 'none' } }} />

        {/* Right controls */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          {/* Lang toggle */}
          <Box sx={{ display: 'flex', border: '1px solid', borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
            {(['en', 'bn'] as const).map((l) => (
              <Button
                key={l}
                onClick={() => setLang(l)}
                size="small"
                sx={{
                  minWidth: 36, height: 30, borderRadius: 0, px: 1,
                  fontSize: '0.75rem', fontWeight: 600,
                  backgroundColor: lang === l ? 'primary.main' : 'transparent',
                  color:           lang === l ? 'primary.contrastText' : 'text.secondary',
                  '&:hover': { backgroundColor: lang === l ? 'primary.dark' : 'action.hover' },
                }}
              >
                {l === 'en' ? 'EN' : 'বাং'}
              </Button>
            ))}
          </Box>

          {/* Theme toggle */}
          <Tooltip title={isDark ? 'Light mode' : 'Dark mode'}>
            <IconButton onClick={toggleTheme} size="small">
              {isDark ? <LightMode fontSize="small" /> : <DarkMode fontSize="small" />}
            </IconButton>
          </Tooltip>

          {/* Chat */}
          <Tooltip title={t('nav.chat')}>
            <IconButton onClick={toggleChat} size="small">
              <Chat fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Admin link */}
          {(user?.role === 'admin' || user?.role === 'superadmin') && (
            <Tooltip title={t('nav.admin')}>
              <IconButton component={Link} to="/admin" size="small">
                <AdminPanelSettings fontSize="small" />
              </IconButton>
            </Tooltip>
          )}

          {/* Auth */}
          {user ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => { clearAuth(); navigate('/'); }}
              sx={{ ml: 0.5 }}
            >
              {t('auth.logout')}
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              component={Link}
              to="/login"
              sx={{ ml: 0.5 }}
            >
              {t('auth.login')}
            </Button>
          )}
        </Box>
      </Toolbar>
    </AppBar>
  );
};
