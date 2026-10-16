import { createTheme, alpha } from '@mui/material/styles';

const PRIMARY      = '#0891B2';
const PRIMARY_DARK = '#155E75';
const PRIMARY_TINT = '#CFFAFE';
const ACCENT       = '#FACC15';

const shared = {
  typography: {
    fontFamily: "'Inter', system-ui, sans-serif",
    h1: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h2: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h3: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    h4: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    h5: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    h6: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    body1: { fontSize: '0.9375rem', lineHeight: 1.6 },
    body2: { fontSize: '0.875rem', lineHeight: 1.5 },
    caption: { fontSize: '0.75rem' },
    overline: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600, letterSpacing: '0.08em' },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiButtonBase:  { defaultProps: { disableRipple: true } },
    MuiButton: {
      defaultProps: { disableRipple: true, disableElevation: true },
      styleOverrides: {
        root: { textTransform: 'none', fontWeight: 600, borderRadius: 8, transition: 'background-color 120ms ease, border-color 120ms ease, color 120ms ease' },
        sizeMedium: { height: 40, padding: '0 16px' },
        sizeLarge:  { height: 48, padding: '0 24px' },
        sizeSmall:  { height: 32, padding: '0 12px', fontSize: '0.8125rem' },
      },
    },
    MuiIconButton: {
      defaultProps: { disableRipple: true },
      styleOverrides: { root: { borderRadius: 8, transition: 'background-color 120ms ease', '&:hover': { backgroundColor: alpha(PRIMARY, 0.08) } } },
    },
    MuiTextField: {
      defaultProps: { variant: 'outlined', size: 'small' },
      styleOverrides: { root: { '& .MuiOutlinedInput-root': { borderRadius: 8, '& fieldset': { borderColor: '#E2E8F0' }, '&:hover fieldset': { borderColor: '#94A3B8' }, '&.Mui-focused fieldset': { borderColor: PRIMARY } } } },
    },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { borderRadius: 12, backgroundImage: 'none' } },
    },
    MuiChip: {
      styleOverrides: { root: { borderRadius: 9999, fontWeight: 500, fontSize: '0.75rem', height: 24, transition: 'background-color 120ms ease' }, label: { padding: '0 10px' } },
    },
    MuiDialog: {
      defaultProps: { TransitionComponent: undefined },
      styleOverrides: { paper: { borderRadius: 16, backgroundImage: 'none' } },
    },
    MuiDrawer:  { styleOverrides: { paper: { backgroundImage: 'none' } } },
    MuiMenu:    { styleOverrides: { paper: { borderRadius: 12, boxShadow: '0 4px 12px 0 rgb(0 0 0 / 0.08)', backgroundImage: 'none' } } },
    MuiMenuItem: { defaultProps: { disableRipple: true }, styleOverrides: { root: { borderRadius: 6, margin: '2px 6px', transition: 'background-color 120ms ease' } } },
    MuiTooltip: { styleOverrides: { tooltip: { fontSize: '0.75rem', borderRadius: 6 } } },
    MuiLinearProgress: { styleOverrides: { root: { borderRadius: 9999, height: 6 } } },
    MuiTabs: { styleOverrides: { indicator: { height: 2, borderRadius: 2 } } },
    MuiTab: { defaultProps: { disableRipple: true }, styleOverrides: { root: { textTransform: 'none', fontWeight: 500, fontFamily: "'Space Grotesk', sans-serif", minHeight: 44 } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 10 } } },
  },
};

export const lightTheme = createTheme({
  ...shared,
  palette: {
    mode: 'light',
    primary:    { main: PRIMARY, dark: PRIMARY_DARK, light: PRIMARY_TINT, contrastText: '#fff' },
    secondary:  { main: ACCENT, contrastText: '#0B1220' },
    error:      { main: '#E11D48' },
    warning:    { main: '#F59E0B' },
    success:    { main: '#16A34A' },
    info:       { main: '#2563EB' },
    background: { default: '#F3F5F8', paper: '#FFFFFF' },
    text:       { primary: '#0B1220', secondary: '#334155', disabled: '#94A3B8' },
    divider:    '#E2E8F0',
  },
  components: {
    ...shared.components as any,
    MuiCard: { defaultProps: { elevation: 0 }, styleOverrides: { root: { borderRadius: 12, border: '1px solid #E2E8F0', backgroundImage: 'none' } } },
    MuiMenu: { styleOverrides: { paper: { borderRadius: 12, border: '1px solid #E2E8F0', boxShadow: '0 4px 12px 0 rgb(0 0 0 / 0.08)', backgroundImage: 'none' } } },
  },
} as any);

export const darkTheme = createTheme({
  ...shared,
  palette: {
    mode: 'dark',
    primary:    { main: '#22D3EE', dark: PRIMARY, light: '#A5F3FC', contrastText: '#0A0F1C' },
    secondary:  { main: ACCENT, contrastText: '#0A0F1C' },
    error:      { main: '#FB7185' },
    warning:    { main: '#FBBF24' },
    success:    { main: '#4ADE80' },
    info:       { main: '#60A5FA' },
    background: { default: '#0A0F1C', paper: '#111827' },
    text:       { primary: '#E6EDF7', secondary: '#A8B8D0', disabled: '#647080' },
    divider:    '#243044',
  },
  components: {
    ...shared.components as any,
    MuiCard: { defaultProps: { elevation: 0 }, styleOverrides: { root: { borderRadius: 12, border: '1px solid #243044', backgroundImage: 'none' } } },
    MuiMenu: { styleOverrides: { paper: { borderRadius: 12, border: '1px solid #243044', boxShadow: '0 4px 12px 0 rgb(0 0 0 / 0.3)', backgroundImage: 'none' } } },
  },
} as any);
