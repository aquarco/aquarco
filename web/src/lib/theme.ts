import { createTheme, type PaletteMode } from '@mui/material/styles'

export function buildTheme(mode: PaletteMode = 'light') {
  return createTheme({
    palette: {
      mode,
      primary: {
        main: '#1976d2',
        light: '#42a5f5',
        dark: '#1565c0',
      },
      secondary: {
        main: '#7c4dff',
      },
      ...(mode === 'dark'
        ? {
            background: {
              default: '#0a0e1a',
              paper: '#12182b',
            },
          }
        : {
            background: {
              default: '#f5f7fa',
              paper: '#ffffff',
            },
          }),
    },
    typography: {
      fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    components: {
      MuiTableCell: {
        styleOverrides: {
          root: {
            fontSize: '0.875rem',
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            fontWeight: 600,
            fontSize: '0.75rem',
          },
        },
      },
    },
  })
}

// Monospace font helper for IDs and code values
export const monoStyle = {
  fontFamily: '"JetBrains Mono", "Fira Code", "Roboto Mono", monospace',
  fontSize: '0.8rem',
} as const

export const defaultTheme = buildTheme('light')
