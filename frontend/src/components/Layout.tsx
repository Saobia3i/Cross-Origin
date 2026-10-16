import React from 'react';
import { Box, BottomNavigation, BottomNavigationAction, Paper } from '@mui/material';
import MapIcon from '@mui/icons-material/Map';
import NotificationsNoneIcon from '@mui/icons-material/NotificationsNone';
import SearchIcon from '@mui/icons-material/Search';
import ChatIcon from '@mui/icons-material/Chat';
import GridViewIcon from '@mui/icons-material/GridView';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Navbar } from './Navbar';
import { useChatStore } from '../store/chat';
import ChatDrawer from '../features/chat/ChatDrawer';

interface Props {
  children: React.ReactNode;
}

const BOTTOM_TABS = [
  { key: 'plan',     path: '/',          Icon: MapIcon },
  { key: 'alerts',   path: '/alerts',    Icon: NotificationsNoneIcon },
  { key: 'simulate', path: '/simulator', Icon: SearchIcon },
  { key: 'network',  path: '/network',   Icon: GridViewIcon },
  { key: 'chat',     path: null,         Icon: ChatIcon },
];

export const Layout: React.FC<Props> = ({ children }) => {
  const location      = useLocation();
  const { t }         = useTranslation();
  const { toggleChat } = useChatStore();
  const activeIdx = BOTTOM_TABS.findIndex(t => t.path && location.pathname === t.path);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <Navbar />

      {/* Main content — offset from Navbar */}
      <Box
        component="main"
        sx={{
          mt: '56px',
          flex: 1,
          pb: { xs: '60px', md: 0 }, // bottom tab bar on mobile
          overflow: 'auto',
        }}
      >
        {children}
      </Box>

      {/* Mobile Bottom Tab Bar */}
      <Paper
        elevation={0}
        sx={{
          display:       { xs: 'block', md: 'none' },
          position:      'fixed',
          bottom:        0,
          left:          0,
          right:         0,
          zIndex:        1200,
          borderTop:     '1px solid',
          borderColor:   'divider',
        }}
      >
        <BottomNavigation
          value={activeIdx}
          sx={{ height: 60, backgroundColor: 'background.paper' }}
        >
          {BOTTOM_TABS.map(({ key, path, Icon }) => (
            <BottomNavigationAction
              key={key}
              label={t(`nav.${key}`)}
              icon={<Icon fontSize="small" />}
              component={path ? Link : 'button'}
              to={path ?? undefined}
              onClick={!path ? toggleChat : undefined}
              disableRipple
              sx={{
                fontSize: '0.65rem',
                minWidth: 0,
                '&.Mui-selected': { color: 'primary.main' },
              }}
            />
          ))}
        </BottomNavigation>
      </Paper>

      {/* Chat drawer */}
      <ChatDrawer />
    </Box>
  );
};

export default Layout;
