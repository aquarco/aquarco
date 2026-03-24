'use client'

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import CircularProgress from '@mui/material/CircularProgress'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import LogoutIcon from '@mui/icons-material/Logout'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'
import {
  GET_AGENT_INSTANCES,
  GET_AGENT_DEFINITIONS,
  GET_REPOSITORIES_WITH_AGENTS,
  CLAUDE_AUTH_STATUS,
  CLAUDE_LOGIN_START,
  CLAUDE_LOGIN_POLL,
  CLAUDE_SUBMIT_CODE,
  CLAUDE_LOGOUT,
  SET_AGENT_DISABLED,
  UPDATE_AGENT_SPEC,
  RESET_AGENT_OVERRIDE,
} from '@/lib/graphql/queries'
import { formatDate, formatNumber } from '@/lib/format'
import GlobalAgentsSection from '@/components/agents/GlobalAgentsSection'
import RepositoryAgentsSection from '@/components/agents/RepositoryAgentsSection'
import AgentEditDialog from '@/components/agents/AgentEditDialog'
import type { AgentDefinition } from '@/components/agents/AgentCard'

interface AgentInstance {
  agentName: string
  activeCount: number
  totalExecutions: number
  totalTokensUsed: number
  lastExecutionAt: string | null
}

function ActiveDot({ active }: { active: boolean }) {
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        backgroundColor: active ? 'success.main' : 'grey.400',
        mr: 1,
        verticalAlign: 'middle',
      }}
    />
  )
}

type LoginStep = 'idle' | 'starting' | 'authorize' | 'paste-code' | 'submitting' | 'done'

export default function AgentsPage() {
  const [tabIndex, setTabIndex] = useState(0)

  // Runtime instances tab
  const { data: instancesData, loading: instancesLoading, error: instancesError } = useQuery(GET_AGENT_INSTANCES)

  // Definitions tab
  const {
    data: defsData,
    loading: defsLoading,
    error: defsError,
    refetch: refetchDefs,
  } = useQuery(GET_AGENT_DEFINITIONS)

  const {
    data: repoAgentsData,
    loading: repoAgentsLoading,
    refetch: refetchRepoAgents,
  } = useQuery(GET_REPOSITORIES_WITH_AGENTS)

  // Auth
  const { data: authData, loading: authLoading, refetch: refetchAuth } = useQuery(CLAUDE_AUTH_STATUS)

  // Edit dialog state
  const [editAgent, setEditAgent] = useState<AgentDefinition | null>(null)
  const [editError, setEditError] = useState<string | null>(null)

  // Mutations
  const [setAgentDisabled] = useMutation(SET_AGENT_DISABLED, {
    onCompleted: () => { refetchDefs(); refetchRepoAgents() },
  })
  const [updateAgentSpec, { loading: savingSpec }] = useMutation(UPDATE_AGENT_SPEC, {
    onCompleted: () => { setEditAgent(null); refetchDefs(); refetchRepoAgents() },
    onError: (err) => setEditError(err.message),
  })
  const [resetAgentOverride] = useMutation(RESET_AGENT_OVERRIDE, {
    onCompleted: () => { refetchDefs(); refetchRepoAgents() },
  })

  // Claude auth
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

  const agents: AgentInstance[] = (instancesData?.agentInstances ?? []).slice().sort(
    (a: AgentInstance, b: AgentInstance) => a.agentName.localeCompare(b.agentName)
  )

  const globalAgents: AgentDefinition[] = (defsData?.agentDefinitions ?? []).filter(
    (a: AgentDefinition) => a.source === 'DEFAULT' || a.source === 'GLOBAL'
  )

  // --- Handlers ---

  function handleToggleDisabledGlobal(agentName: string, isDisabled: boolean) {
    setAgentDisabled({
      variables: { input: { agentName, isDisabled, scope: 'global', scopeRepository: null } },
    })
  }

  function handleToggleDisabledRepo(agentName: string, isDisabled: boolean, scopeRepository: string) {
    setAgentDisabled({
      variables: { input: { agentName, isDisabled, scope: 'repository', scopeRepository } },
    })
  }

  function handleEdit(agent: AgentDefinition) {
    setEditError(null)
    setEditAgent(agent)
  }

  function handleSaveSpec(agentName: string, spec: unknown, scope: string, scopeRepository: string | null) {
    updateAgentSpec({
      variables: { input: { agentName, spec, scope, scopeRepository } },
    })
  }

  function handleReset(agent: AgentDefinition) {
    const scope = agent.source === 'REPOSITORY' ? 'repository' : 'global'
    resetAgentOverride({
      variables: { agentName: agent.name, scope, scopeRepository: agent.sourceRepository ?? null },
    })
  }

  // Claude auth handlers
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

      <Tabs value={tabIndex} onChange={(_, v) => setTabIndex(v)} sx={{ mb: 2 }}>
        <Tab label="Definitions" />
        <Tab label="Runtime Instances" />
      </Tabs>

      {/* Tab 0: Agent Definitions (Global + Repository) */}
      {tabIndex === 0 && (
        <Stack spacing={3}>
          {(defsError) && (
            <Alert severity="error" sx={{ mb: 2 }}>
              Failed to load agent definitions: {defsError?.message}
            </Alert>
          )}

          <GlobalAgentsSection
            agents={globalAgents}
            loading={defsLoading}
            onToggleDisabled={handleToggleDisabledGlobal}
            onEdit={handleEdit}
            onReset={handleReset}
          />

          <RepositoryAgentsSection
            repositoriesWithAgents={repoAgentsData?.repositoriesWithAgents ?? []}
            loading={repoAgentsLoading}
            onToggleDisabled={handleToggleDisabledRepo}
            onEdit={handleEdit}
            onReset={handleReset}
          />
        </Stack>
      )}

      {/* Tab 1: Runtime Instances (original table) */}
      {tabIndex === 1 && (
        <>
          {instancesError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              Failed to load agents: {instancesError.message}
            </Alert>
          )}

          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Agent Name</TableCell>
                  <TableCell align="right">Active Instances</TableCell>
                  <TableCell align="right">Total Executions</TableCell>
                  <TableCell align="right">Total Tokens Used</TableCell>
                  <TableCell>Last Execution</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {instancesLoading
                  ? [...Array(6)].map((_, i) => (
                      <TableRow key={i}>
                        {[...Array(5)].map((_, j) => (
                          <TableCell key={j}>
                            <Skeleton variant="text" />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  : agents.map((agent) => (
                      <TableRow key={agent.agentName} data-testid={`agent-row-${agent.agentName}`}>
                        <TableCell>
                          <ActiveDot active={agent.activeCount > 0} />
                          {agent.agentName}
                        </TableCell>
                        <TableCell align="right">
                          <Typography
                            variant="body2"
                            fontWeight={agent.activeCount > 0 ? 700 : 400}
                            color={agent.activeCount > 0 ? 'success.main' : 'text.primary'}
                          >
                            {agent.activeCount}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">{formatNumber(agent.totalExecutions)}</TableCell>
                        <TableCell align="right">{formatNumber(agent.totalTokensUsed)}</TableCell>
                        <TableCell>{formatDate(agent.lastExecutionAt)}</TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}

      {/* Agent Edit Dialog */}
      <AgentEditDialog
        open={editAgent !== null}
        agent={editAgent}
        onClose={() => setEditAgent(null)}
        onSave={handleSaveSpec}
        saving={savingSpec}
        error={editError}
      />

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
