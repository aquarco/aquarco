'use client'

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Alert from '@mui/material/Alert'
import CircularProgress from '@mui/material/CircularProgress'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import LogoutIcon from '@mui/icons-material/Logout'
import {
  CLAUDE_AUTH_STATUS,
  CLAUDE_LOGIN_START,
  CLAUDE_LOGIN_POLL,
  CLAUDE_SUBMIT_CODE,
  CLAUDE_LOGOUT,
} from '@/lib/graphql/queries'
import GlobalAgentsTab from '@/components/agents/GlobalAgentsTab'
import RepoAgentsTab from '@/components/agents/RepoAgentsTab'

interface TabPanelProps {
  children?: React.ReactNode
  index: number
  value: number
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return (
    <Box role="tabpanel" hidden={value !== index} sx={{ pt: 2 }}>
      {value === index && children}
    </Box>
  )
}

type LoginStep = 'idle' | 'starting' | 'authorize' | 'paste-code' | 'submitting' | 'done'

export default function AgentsPage() {
  const { data: authData, loading: authLoading, refetch: refetchAuth } = useQuery(CLAUDE_AUTH_STATUS)

  const [tabIndex, setTabIndex] = useState(0)
  const [loginDialogOpen, setLoginDialogOpen] = useState(false)
  const [loginStep, setLoginStep] = useState<LoginStep>('idle')
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null)
  const [authCode, setAuthCode] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginSuccess, setLoginSuccess] = useState<string | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const loginStepRef = useRef<LoginStep>('idle')

  const [claudeLoginStart] = useMutation(CLAUDE_LOGIN_START)
  const [claudeLoginPoll] = useMutation(CLAUDE_LOGIN_POLL)
  const [claudeSubmitCode] = useMutation(CLAUDE_SUBMIT_CODE)
  const [claudeLogout] = useMutation(CLAUDE_LOGOUT, {
    onCompleted: () => refetchAuth(),
  })

  const updateLoginStep = useCallback((step: LoginStep) => {
    loginStepRef.current = step
    setLoginStep(step)
  }, [])

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  async function handleClaudeLogin() {
    setLoginError(null)
    setLoginSuccess(null)
    setAuthorizeUrl(null)
    setAuthCode('')
    updateLoginStep('starting')
    setLoginDialogOpen(true)

    try {
      const { data } = await claudeLoginStart()
      const result = data?.claudeLoginStart
      if (!result?.authorizeUrl) {
        setLoginError('Failed to start login flow. Is the claude-auth-helper running on the host?')
        updateLoginStep('idle')
        return
      }
      setAuthorizeUrl(result.authorizeUrl)
      updateLoginStep('authorize')
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Failed to start login')
      updateLoginStep('idle')
    }
  }

  function handleOpenedAuthPage() {
    updateLoginStep('paste-code')
  }

  async function handleSubmitCode() {
    if (!authCode.trim()) return
    updateLoginStep('submitting')
    setLoginError(null)

    try {
      const { data } = await claudeSubmitCode({ variables: { code: authCode.trim() } })
      const result = data?.claudeSubmitCode

      if (result?.success) {
        setLoginSuccess('Login successful')
        updateLoginStep('done')
        refetchAuth()
      } else if (result?.error) {
        setLoginError(result.error)
        updateLoginStep('paste-code')
      } else {
        // Start polling auth status to confirm
        pollTimerRef.current = setInterval(async () => {
          try {
            const { data: pollData } = await claudeLoginPoll()
            const pollResult = pollData?.claudeLoginPoll
            if (pollResult?.success) {
              stopPolling()
              setLoginSuccess(pollResult.email ? `Logged in as ${pollResult.email}` : 'Login successful')
              updateLoginStep('done')
              refetchAuth()
            }
          } catch {
            // keep trying
          }
        }, 3000)

        // Stop polling after 30s (use ref to avoid stale closure)
        setTimeout(() => {
          if (loginStepRef.current === 'submitting') {
            stopPolling()
            setLoginError('Login verification timed out. Please try again.')
            updateLoginStep('paste-code')
          }
        }, 30000)
      }
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Failed to submit code')
      updateLoginStep('paste-code')
    }
  }

  function handleLoginDialogClose() {
    stopPolling()
    setLoginDialogOpen(false)
    updateLoginStep('idle')
    setAuthorizeUrl(null)
    setAuthCode('')
    setLoginError(null)
    setLoginSuccess(null)
  }

  const claudeAuth = authData?.claudeAuthStatus

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5" fontWeight={700}>
          Agents
        </Typography>
        <Stack direction="row" spacing={1}>
          {claudeAuth?.authenticated ? (
            <Button
              variant="outlined"
              color="success"
              startIcon={<SmartToyIcon />}
              endIcon={<LogoutIcon />}
              onClick={() => claudeLogout()}
              data-testid="btn-claude-logout"
            >
              Claude: {claudeAuth.email ?? 'connected'}
            </Button>
          ) : (
            <Button
              variant="outlined"
              startIcon={authLoading ? <CircularProgress size={18} /> : <SmartToyIcon />}
              onClick={handleClaudeLogin}
              disabled={authLoading}
              data-testid="btn-claude-login"
            >
              Claude Login
            </Button>
          )}
        </Stack>
      </Stack>

      <Tabs
        value={tabIndex}
        onChange={(_, newValue) => setTabIndex(newValue)}
        sx={{ borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab label="Global Agents" data-testid="tab-global-agents" />
        <Tab label="Repository Agents" data-testid="tab-repo-agents" />
      </Tabs>

      <TabPanel value={tabIndex} index={0}>
        <GlobalAgentsTab />
      </TabPanel>

      <TabPanel value={tabIndex} index={1}>
        <RepoAgentsTab />
      </TabPanel>

      {/* Claude Login dialog */}
      <Dialog open={loginDialogOpen} onClose={handleLoginDialogClose} maxWidth="sm" fullWidth>
        <DialogTitle>
          <Stack direction="row" alignItems="center" spacing={1}>
            <SmartToyIcon />
            <span>Claude Login</span>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {loginError && <Alert severity="error">{loginError}</Alert>}
            {loginSuccess && (
              <Alert severity="success" icon={<CheckCircleIcon />}>
                {loginSuccess}
              </Alert>
            )}

            {loginStep === 'starting' && (
              <Stack alignItems="center" sx={{ py: 2 }}>
                <CircularProgress />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  Starting login flow...
                </Typography>
              </Stack>
            )}

            {loginStep === 'authorize' && (
              <>
                <Typography variant="body2">
                  <strong>Step 1:</strong> Click below to sign in with your Claude account.
                  After authorizing, you will see an authentication code.
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<SmartToyIcon />}
                  href={authorizeUrl!}
                  target="_blank"
                  rel="noopener"
                  onClick={handleOpenedAuthPage}
                  data-testid="btn-claude-authorize"
                >
                  Sign in to Claude
                </Button>
              </>
            )}

            {loginStep === 'paste-code' && (
              <>
                <Typography variant="body2">
                  <strong>Step 2:</strong> Paste the authentication code from Claude below:
                </Typography>
                <TextField
                  label="Authentication Code"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  fullWidth
                  autoFocus
                  placeholder="Paste the code from Claude..."
                  inputProps={{
                    'data-testid': 'input-claude-auth-code',
                    style: { fontFamily: 'monospace', fontSize: '0.85rem' },
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && authCode.trim()) handleSubmitCode()
                  }}
                />
                <Button
                  variant="contained"
                  onClick={handleSubmitCode}
                  disabled={!authCode.trim()}
                  data-testid="btn-claude-submit-code"
                >
                  Complete Login
                </Button>
              </>
            )}

            {loginStep === 'submitting' && (
              <Stack alignItems="center" sx={{ py: 2 }}>
                <CircularProgress />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  Verifying authentication...
                </Typography>
              </Stack>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleLoginDialogClose}>
            {loginStep === 'done' ? 'Done' : 'Cancel'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
