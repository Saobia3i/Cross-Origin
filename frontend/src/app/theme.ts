import { createTheme } from '@mui/material/styles';

// ── Shared overrides (ripple-free, tight border radius) ─────────────────
const sharedComponents = {
  MuiButtonBase: { defaultProps: { disableRipple: true } },
  MuiButton: {
    styleOverrides: {
      root: {
        textTransform: 'none' as const,
        fontFamily: "'Space Grotesk', sans-serif",
        fontWeight: 600,
        borderRadius: 8,
        transition: 'background-color 120ms ease, border-color 120ms ease, color 120ms ease',
        boxShadow: 'none',
        '&:hover': { boxShadow: 'none' },
        '&:active': { boxShadow: 'none' },
      },
      // Yellow buttons get dark text
      containedPrimary: {
        color: '#1A1600',
        '&:hover': { color: '#1A1600' },
      },
    },
  },
  MuiChip: {
    styleOverrides: {
      root: { borderRadius: 6, fontWeight: 500 },
    },
  },
  MuiPaper: {
    styleOverrides: {
      root: { borderRadius: 10 },
    },
  },
  MuiTextField: {
    defaultProps: { variant: 'outlined' as const },
    styleOverrides: {
      root: { '& .MuiOutlinedInput-root': { borderRadius: 8 } },
    },
  },
  MuiTab: {
    styleOverrides: {
      root: { textTransform: 'none' as const, fontWeight: 600 },
    },
  },
};

// ── Light Theme — "Daytime Road" ─────────────────────────────────────────
export const lightTheme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main:          '#E6B800',   // road-marking yellow
      dark:          '#BF9A00',
      light:         '#FFF8D6',
      contrastText:  '#1A1600',   // dark text on yellow
    },
    secondary: {
      main:         '#2E7D4F',    // signal green
      light:        '#D6EFDF',
      contrastText: '#FFFFFF',
    },
    success: {
      main:  '#2E7D4F',
      light: '#D6EFDF',
    },
    warning: {
      main:  '#E6B800',
      light: '#FFF8D6',
    },
    error: {
      main:  '#C0392B',
      light: '#FDECEA',
    },
    info: {
      main: '#1A6FA8',
    },
    background: {
      default: '#CCCBC6',   // asphalt road surface
      paper:   '#F2F0EA',   // concrete card
    },
    text: {
      primary:   '#1A1A14',
      secondary: '#4A4840',
      disabled:  '#7A7668',
    },
    divider: '#B4B0A5',
  },
  typography: {
    fontFamily: "'Inter', system-ui, sans-serif",
    h1: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h2: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h3: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h4: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h5: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    h6: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    button: { fontFamily: "'Space Grotesk', sans-serif" },
  },
  components: {
    ...sharedComponents,
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#3A3930',  // dark asphalt navbar
          color:           '#F2F0EA',
          borderBottom:    '2px solid #E6B800', // yellow stripe
        },
      },
    },
    MuiBottomNavigation: {
      styleOverrides: {
        root: {
          backgroundColor: '#3A3930',
          borderTop: '2px solid #E6B800',
        },
      },
    },
    MuiBottomNavigationAction: {
      styleOverrides: {
        root: {
          color: '#B0AC98',
          '&.Mui-selected': { color: '#E6B800' },
        },
      },
    },
  } as any,
});

// ── Dark Theme — "Night Road" ─────────────────────────────────────────────
export const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main:         '#F0C800',
      dark:         '#C9A800',
      light:        '#332B00',
      contrastText: '#1A1600',
    },
    secondary: {
      main:         '#3DAE6A',
      light:        '#1A3A28',
      contrastText: '#FFFFFF',
    },
    success: {
      main:  '#3DAE6A',
      light: '#1A3A28',
    },
    warning: {
      main:  '#F0C800',
      light: '#332B00',
    },
    error: {
      main:  '#E55347',
      light: '#3A1A18',
    },
    info: {
      main: '#4D9FCC',
    },
    background: {
      default: '#1C1C18',   // night asphalt
      paper:   '#28271F',   // dark concrete card
    },
    text: {
      primary:   '#EDE9DA',
      secondary: '#B0AC98',
      disabled:  '#726E5A',
    },
    divider: '#44433A',
  },
  typography: {
    fontFamily: "'Inter', system-ui, sans-serif",
    h1: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h2: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h3: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h4: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700 },
    h5: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    h6: { fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600 },
    button: { fontFamily: "'Space Grotesk', sans-serif" },
  },
  components: {
    ...sharedComponents,
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#141410',
          color:           '#EDE9DA',
          borderBottom:    '2px solid #F0C800',
        },
      },
    },
    MuiBottomNavigation: {
      styleOverrides: {
        root: {
          backgroundColor: '#141410',
          borderTop: '2px solid #F0C800',
        },
      },
    },
    MuiBottomNavigationAction: {
      styleOverrides: {
        root: {
          color: '#726E5A',
          '&.Mui-selected': { color: '#F0C800' },
        },
      },
    },
  } as any,
});
