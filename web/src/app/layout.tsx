'use client'

import { type ReactNode, useState } from 'react'
import { ThemeProvider } from '@mui/material/styles'
import CssBaseline from '@mui/material/CssBaseline'
import Box from '@mui/material/Box'
import Drawer from '@mui/material/Drawer'
import AppBar from '@mui/material/AppBar'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import List from '@mui/material/List'
import ListItem from '@mui/material/ListItem'
import ListItemButton from '@mui/material/ListItemButton'
import ListItemIcon from '@mui/material/ListItemIcon'
import ListItemText from '@mui/material/ListItemText'
import DashboardIcon from '@mui/icons-material/Dashboard'
import AssignmentIcon from '@mui/icons-material/Assignment'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import FolderIcon from '@mui/icons-material/Folder'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import NetworkCheckIcon from '@mui/icons-material/NetworkCheck'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ApolloWrapper } from '@/lib/apollo'
import { defaultTheme } from '@/lib/theme'

const DRAWER_WIDTH = 240

const NAV_ITEMS = [
  { label: 'Dashboard', href: '/', icon: <DashboardIcon /> },
  { label: 'Tasks', href: '/tasks', icon: <AssignmentIcon /> },
  { label: 'Agents', href: '/agents', icon: <SmartToyIcon /> },
  { label: 'Repositories', href: '/repos', icon: <FolderIcon /> },
  { label: 'Pipelines', href: '/pipelines', icon: <AccountTreeIcon /> },
  { label: 'Network', href: '/network', icon: <NetworkCheckIcon /> },
]

interface RootLayoutProps {
  children: ReactNode
}

function AppShell({ children }: RootLayoutProps) {
  const pathname = usePathname()

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}
      >
        <Toolbar>
          <Typography variant="h6" noWrap component="div" fontWeight={700}>
            AI Fishtank
          </Typography>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
          },
        }}
      >
        <Toolbar />
        <Box sx={{ overflow: 'auto', pt: 1 }}>
          <List disablePadding>
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === '/'
                  ? pathname === '/'
                  : pathname.startsWith(item.href)
              return (
                <ListItem key={item.href} disablePadding>
                  <ListItemButton
                    component={Link}
                    href={item.href}
                    selected={active}
                    data-testid={`nav-${item.label.toLowerCase()}`}
                    sx={{
                      mx: 1,
                      borderRadius: 1,
                      '&.Mui-selected': {
                        backgroundColor: 'primary.main',
                        color: 'primary.contrastText',
                        '& .MuiListItemIcon-root': {
                          color: 'primary.contrastText',
                        },
                        '&:hover': {
                          backgroundColor: 'primary.dark',
                        },
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 40 }}>{item.icon}</ListItemIcon>
                    <ListItemText primary={item.label} />
                  </ListItemButton>
                </ListItem>
              )
            })}
          </List>
        </Box>
      </Drawer>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 3,
          backgroundColor: 'background.default',
          minHeight: '100vh',
        }}
      >
        <Toolbar />
        {children}
      </Box>
    </Box>
  )
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en">
      <body>
        <ThemeProvider theme={defaultTheme}>
          <CssBaseline />
          <ApolloWrapper>
            <AppShell>{children}</AppShell>
          </ApolloWrapper>
        </ThemeProvider>
      </body>
    </html>
  )
}
