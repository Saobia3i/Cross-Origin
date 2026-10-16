import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar, Toolbar, Box, IconButton, Button, Tooltip, Typography,
} from '@mui/material';
import MapIcon from '@mui/icons-material/Map';
import SearchIcon from '@mui/icons-material/Search';
import NotificationsNoneIcon from '@mui/icons-material/NotificationsNone';
import ChatIcon from '@mui/icons-material/Chat';
import GridViewIcon from '@mui/icons-material/GridView';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import AdminPanelSettingsIcon from '@mui/icons-material/AdminPanelSettings';
import GroupsIcon from '@mui/icons-material/Groups';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useUIStore } from '../store/ui';
import { useAuthStore } from '../store/auth';
import { useChatStore } from '../store/chat';

const NAV = [
  { key: 'plan',     path: '/',          Icon: MapIcon },
  { key: 'alerts',   path: '/alerts',    Icon: NotificationsNoneIcon },
  { key: 'simulate', path: '/simulator', Icon: SearchIcon },
  { key: 'network',  path: '/network',   Icon: GridViewIcon },
  { key: 'community', path: '/community', Icon: GroupsIcon },
];

export const Navbar: React.FC = () => {
  const { t }         = useTranslation();
  const { theme, lang, toggleTheme, setLang } = useUIStore();
  const { user, clearAuth } = useAuthStore();
  const { toggleChat } = useChatStore();
  const location      = useLocation();
  const navigate      = useNavigate();
  const qc            = useQueryClient();

  const isDark = theme === 'dark';
  const navItems = user?.role === 'admin'
    ? [...NAV, { key: 'admin', path: '/admin', Icon: AdminPanelSettingsIcon }]
    : NAV;

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
            width: 32, height: 32, borderRadius: 1,
            background: '#E6B800',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, border: '2px solid #BF9A00',
          }}>🛣</Box>
          <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, color: '#F2F0EA', display: { xs: 'none', sm: 'block' } }}>
            DhakaPath
          </Typography>
        </Box>

        {/* Desktop Nav */}
        <Box sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'center', gap: 0.5, flex: 1 }}>
          {navItems.map(({ key, path, Icon }) => {
            const active = location.pathname === path;
            return (
              <Button
                key={key}
                component={Link}
                to={path}
                startIcon={<Icon sx={{ fontSize: 18 }} />}
                sx={{
                  color:           active ? '#E6B800' : '#D4D0C8',
                  backgroundColor: active ? 'rgba(230,184,0,0.12)' : 'transparent',
                  fontWeight:      active ? 600 : 400,
                  '&:hover':       { backgroundColor: 'rgba(230,184,0,0.1)', color: '#E6B800' },
                  px: 1.5, py: 0.75,
                }}
              >
                {key === 'community' ? 'Community' : t(`nav.${key}`)}
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
              {isDark ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
            </IconButton>
          </Tooltip>

          {/* Chat */}
          <Tooltip title={t('nav.chat')}>
            <IconButton onClick={toggleChat} size="small">
              <ChatIcon fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Signed-in user */}
          {user && (
            <Typography variant="caption" sx={{ color: '#D4D0C8', display: { xs: 'none', sm: 'block' }, mx: 0.5 }}>
              {user.name}{user.role === 'admin' ? ' (admin)' : ''}
            </Typography>
          )}

          {/* Auth */}
          {user ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => { clearAuth(); qc.invalidateQueries(); navigate('/'); }}
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
