'use client'

import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import Link from '@mui/material/Link'
import CircularProgress from '@mui/material/CircularProgress'
import GitHubIcon from '@mui/icons-material/GitHub'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Button from '@mui/material/Button'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Chip, { type ChipProps } from '@mui/material/Chip'
import Collapse from '@mui/material/Collapse'
import Stack from '@mui/material/Stack'
import AddIcon from '@mui/icons-material/Add'
import IconButton from '@mui/material/IconButton'
import DeleteIcon from '@mui/icons-material/Delete'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp'
import VpnKeyIcon from '@mui/icons-material/VpnKey'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import ClearIcon from '@mui/icons-material/Clear'
import InputAdornment from '@mui/material/InputAdornment'
import Tooltip from '@mui/material/Tooltip'
import RefreshIcon from '@mui/icons-material/Refresh'
import LogoutIcon from '@mui/icons-material/Logout'
import Autocomplete from '@mui/material/Autocomplete'
import LockIcon from '@mui/icons-material/Lock'
import Switch from '@mui/material/Switch'
import FormControlLabel from '@mui/material/FormControlLabel'
import FormGroup from '@mui/material/FormGroup'
import Checkbox from '@mui/material/Checkbox'
import SettingsIcon from '@mui/icons-material/Settings'
import Snackbar from '@mui/material/Snackbar'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import { GET_REPOSITORIES, REGISTER_REPOSITORY, REMOVE_REPOSITORY, RETRY_CLONE, SET_CONFIG_REPO, GITHUB_AUTH_STATUS, GITHUB_LOGIN_START, GITHUB_LOGIN_POLL, GITHUB_LOGOUT, GITHUB_REPOSITORIES, RELOAD_REPO_AGENTS, GET_REPO_AGENT_SCAN } from '@/lib/graphql/queries'
import { formatDate } from '@/lib/format'

interface RepoAgentScanInfo {
  id: string
  status: string
  agentsFound: number
  agentsCreated: number
  createdAt: string
}

interface Repository {
  name: string
  url: string
  branch: string
  cloneDir: string
  isConfigRepo: boolean
  cloneStatus: string
  lastPulledAt: string | null
  errorMessage: string | null
  deployPublicKey: string | null
  taskCount: number
  hasClaudeAgents: boolean
  lastAgentScan: RepoAgentScanInfo | null
}

function getCloneStatusColor(status: string): ChipProps['color'] {
  switch (status?.toUpperCase()) {
    case 'READY': return 'success'
    case 'PENDING': return 'default'
    case 'CLONING': return 'warning'
    case 'ERROR': return 'error'
    default: return 'default'
  }
}

interface GithubRepo {
  nameWithOwner: string
  url: string
  defaultBranch: string
  isPrivate: boolean
  description: string | null
}

const AVAILABLE_POLLERS = [
  { value: 'github-tasks', label: 'Issues' },
  { value: 'github-source', label: 'PRs and commits' },
] as const
const DEFAULT_POLLERS = ['github-tasks', 'github-source']

interface AddRepoFormState {
  name: string
  url: string
  defaultBranch: string | null
  pollers: string[]
  isConfigRepo: boolean
}

const EMPTY_FORM: AddRepoFormState = { name: '', url: '', defaultBranch: null, pollers: [...DEFAULT_POLLERS], isConfigRepo: false }

export default function ReposPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState<AddRepoFormState>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [collapsedErrors, setCollapsedErrors] = useState<Set<string>>(new Set())
  const [copiedKey, setCopiedKey] = useState(false)
  const [copiedCode, setCopiedCode] = useState(false)
  const [loginDialogOpen, setLoginDialogOpen] = useState(false)
  const [deviceCode, setDeviceCode] = useState<{ userCode: string; verificationUri: string } | null>(null)
  const [githubOpened, setGithubOpened] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginSuccess, setLoginSuccess] = useState<string | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const { data, loading, error, refetch } = useQuery(GET_REPOSITORIES)
  const { data: authData, loading: authLoading, refetch: refetchAuth } = useQuery(GITHUB_AUTH_STATUS)
  const isGithubAuthenticated = authData?.githubAuthStatus?.authenticated === true
  const { data: ghReposData, loading: ghReposLoading } = useQuery(GITHUB_REPOSITORIES, {
    skip: !isGithubAuthenticated,
  })
  const githubRepos: GithubRepo[] = ghReposData?.githubRepositories ?? []

  // Poll while any repo is in a non-terminal state
  const repositories: Repository[] = data?.repositories ?? []
  const hasActiveRepo = repositories.some(
    (r) => r.cloneStatus === 'PENDING' || r.cloneStatus === 'CLONING'
  )
  useEffect(() => {
    if (!hasActiveRepo) return
    const timer = setInterval(() => refetch(), 3000)
    return () => clearInterval(timer)
  }, [hasActiveRepo, refetch])

  const [registerRepository, { loading: registering }] = useMutation(
    REGISTER_REPOSITORY,
    {
      onCompleted: (result) => {
        const errors = result?.registerRepository?.errors
        if (errors?.length) {
          setFormError(errors.map((e: { message: string }) => e.message).join(', '))
        } else {
          setDialogOpen(false)
          setForm(EMPTY_FORM)
          setFormError(null)
          refetch()
        }
      },
      onError: (err) => {
        setFormError(err.message)
      },
    }
  )

  const [removeRepository] = useMutation(REMOVE_REPOSITORY, {
    onCompleted: () => refetch(),
  })

  const [githubLoginStart] = useMutation(GITHUB_LOGIN_START)
  const [githubLoginPoll] = useMutation(GITHUB_LOGIN_POLL)
  const [githubLogout] = useMutation(GITHUB_LOGOUT, {
    onCompleted: () => refetchAuth(),
  })

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  async function handleGithubLogin() {
    setLoginError(null)
    setLoginSuccess(null)
    setDeviceCode(null)
    setGithubOpened(false)
    setLoginDialogOpen(true)

    try {
      const { data } = await githubLoginStart()
      const code = data?.githubLoginStart
      if (!code) {
        setLoginError('Failed to start login flow')
        return
      }
      setDeviceCode({ userCode: code.userCode, verificationUri: code.verificationUri })

      // Start sequential polling (backend enforces GitHub's rate limit via delay)
      async function poll() {
        if (!pollTimerRef.current) return
        try {
          const { data: pollData } = await githubLoginPoll()
          if (!pollTimerRef.current) return
          const result = pollData?.githubLoginPoll

          if (result?.success) {
            stopPolling()
            setLoginSuccess(result.username ? `Logged in as ${result.username}` : 'Login successful')
            setDeviceCode(null)
            refetchAuth()
            return
          } else if (result?.error) {
            stopPolling()
            setLoginError(result.error)
            setDeviceCode(null)
            return
          }
        } catch {
          // poll failed, keep trying
        }
        if (pollTimerRef.current) pollTimerRef.current = setTimeout(poll, 1000)
      }
      pollTimerRef.current = setTimeout(poll, 1000)
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Failed to start login')
    }
  }

  function handleLoginDialogClose() {
    stopPolling()
    setLoginDialogOpen(false)
    setDeviceCode(null)
    setLoginError(null)
    setLoginSuccess(null)
  }

  const [snackbarMsg, setSnackbarMsg] = useState<string | null>(null)
  const [scanningRepos, setScanningRepos] = useState<Set<string>>(new Set())

  const [reloadRepoAgents] = useMutation(RELOAD_REPO_AGENTS, {
    onCompleted: (result) => {
      const errors = result?.reloadRepoAgents?.errors
      if (errors?.length) {
        setSnackbarMsg(errors.map((e: { message: string }) => e.message).join(', '))
        return
      }
      const scan = result?.reloadRepoAgents?.scan
      if (scan) {
        setSnackbarMsg(`Agent scan started for ${scan.repoName}`)
        setScanningRepos((prev) => new Set([...prev, scan.repoName]))
        // Poll for scan completion
        const pollInterval = setInterval(async () => {
          const { data: scanData } = await refetch()
          const repo = scanData?.repositories?.find((r: Repository) => r.name === scan.repoName)
          const latestScan = repo?.lastAgentScan
          if (latestScan && (latestScan.status === 'COMPLETED' || latestScan.status === 'FAILED')) {
            clearInterval(pollInterval)
            setScanningRepos((prev) => {
              const next = new Set(prev)
              next.delete(scan.repoName)
              return next
            })
            if (latestScan.status === 'COMPLETED') {
              setSnackbarMsg(`Scan complete: ${latestScan.agentsCreated} agent(s) loaded for ${scan.repoName}`)
            } else {
              setSnackbarMsg(`Scan failed for ${scan.repoName}`)
            }
          }
        }, 2000)
        // Auto-cleanup after 2 minutes
        setTimeout(() => clearInterval(pollInterval), 120_000)
      }
    },
    onError: (err) => {
      setSnackbarMsg(err.message)
    },
  })

  const [retryClone] = useMutation(RETRY_CLONE, {
    onCompleted: () => refetch(),
  })

  const [setConfigRepo] = useMutation(SET_CONFIG_REPO, {
    onCompleted: () => refetch(),
  })

  function handleSubmit() {
    if (!form.name.trim() || !form.url.trim()) {
      setFormError('Name and URL are required.')
      return
    }
    registerRepository({
      variables: {
        input: {
          name: form.name.trim(),
          url: form.url.trim(),
          branch: form.defaultBranch || undefined,
          pollers: form.pollers,
          isConfigRepo: form.isConfigRepo,
        },
      },
    })
  }

  function handleClose() {
    setDialogOpen(false)
    setForm(EMPTY_FORM)
    setFormError(null)
  }

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5" fontWeight={700}>
          Repositories
        </Typography>
        <Stack direction="row" spacing={1}>
          {authData?.githubAuthStatus?.authenticated ? (
            <Button
              variant="outlined"
              color="success"
              startIcon={<GitHubIcon />}
              endIcon={<LogoutIcon />}
              onClick={() => { if (confirm('Logout from GitHub?')) githubLogout() }}
              data-testid="btn-github-logout"
            >
              {authData.githubAuthStatus.username ?? 'Connected'}
            </Button>
          ) : (
            <Button
              variant="outlined"
              startIcon={authLoading ? <CircularProgress size={18} /> : <GitHubIcon />}
              onClick={handleGithubLogin}
              disabled={authLoading}
              data-testid="btn-github-login"
            >
              GitHub Login
            </Button>
          )}
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setDialogOpen(true)}
            data-testid="btn-add-repository"
          >
            Add Repository
          </Button>
        </Stack>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load repositories: {error.message}
        </Alert>
      )}

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>URL</TableCell>
              <TableCell>Branch</TableCell>
              <TableCell>Clone Status</TableCell>
              <TableCell>Config</TableCell>
              <TableCell>Last Pulled</TableCell>
              <TableCell align="right">Tasks</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {loading
              ? [...Array(4)].map((_, i) => (
                  <TableRow key={i}>
                    {[...Array(7)].map((_, j) => (
                      <TableCell key={j}>
                        <Skeleton variant="text" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : repositories.map((repo) => {
                  const isError = repo.cloneStatus?.toUpperCase() === 'ERROR'
                  const isExpanded = !collapsedErrors.has(repo.name)
                  const isSshUrl = repo.url.startsWith('git@') || repo.url.includes('ssh://')
                  return (
                    <React.Fragment key={repo.name}>
                      <TableRow
                        data-testid={`repo-row-${repo.name}`}
                        sx={isError ? { cursor: 'pointer' } : undefined}
                        onClick={isError ? () => setCollapsedErrors((prev) => {
                          const next = new Set(prev)
                          if (next.has(repo.name)) next.delete(repo.name)
                          else next.add(repo.name)
                          return next
                        }) : undefined}
                      >
                        <TableCell>
                          <Stack direction="row" alignItems="center" spacing={0.5}>
                            {isError && (
                              <IconButton size="small" sx={{ p: 0 }}>
                                {isExpanded ? <KeyboardArrowUpIcon fontSize="small" /> : <KeyboardArrowDownIcon fontSize="small" />}
                              </IconButton>
                            )}
                            <span>{repo.name}</span>
                          </Stack>
                        </TableCell>
                        <TableCell
                          sx={{
                            maxWidth: 280,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {repo.url}
                        </TableCell>
                        <TableCell>{repo.branch}</TableCell>
                        <TableCell>
                          <Chip
                            label={repo.cloneStatus}
                            color={getCloneStatusColor(repo.cloneStatus)}
                            size="small"
                          />
                        </TableCell>
                        <TableCell sx={{ py: 0 }}>
                          <Tooltip title={repo.isConfigRepo ? 'Global config repo' : 'Mark as config repo'}>
                            <Switch
                              size="small"
                              checked={repo.isConfigRepo}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => {
                                setConfigRepo({ variables: { name: repo.name, isConfigRepo: e.target.checked } })
                              }}
                              data-testid={`toggle-config-repo-${repo.name}`}
                            />
                          </Tooltip>
                        </TableCell>
                        <TableCell>{formatDate(repo.lastPulledAt)}</TableCell>
                        <TableCell align="right">{repo.taskCount ?? 0}</TableCell>
                        <TableCell align="right" sx={{ py: 0 }}>
                          <Stack direction="row" spacing={0} justifyContent="flex-end">
                            {repo.hasClaudeAgents && (
                              <Tooltip title={
                                scanningRepos.has(repo.name)
                                  ? 'Scanning agents...'
                                  : 'Reload .claude agents'
                              }>
                                <span>
                                  <IconButton
                                    size="small"
                                    color="primary"
                                    disabled={scanningRepos.has(repo.name) || repo.cloneStatus !== 'READY'}
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      reloadRepoAgents({ variables: { repoName: repo.name } })
                                    }}
                                    data-testid={`btn-reload-agents-${repo.name}`}
                                  >
                                    {scanningRepos.has(repo.name) ? (
                                      <CircularProgress size={18} />
                                    ) : (
                                      <SmartToyIcon fontSize="small" />
                                    )}
                                  </IconButton>
                                </span>
                              </Tooltip>
                            )}
                            <IconButton
                              size="small"
                              color="error"
                              onClick={(e) => {
                                e.stopPropagation()
                                if (confirm(`Remove repository "${repo.name}"?`)) {
                                  removeRepository({ variables: { name: repo.name } })
                                }
                              }}
                              data-testid={`btn-remove-repo-${repo.name}`}
                            >
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </Stack>
                        </TableCell>
                      </TableRow>
                      {isError && (
                        <TableRow>
                          <TableCell colSpan={8} sx={{ py: 0, borderBottom: isExpanded ? undefined : 'none' }}>
                            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                              <Alert
                                severity="error"
                                icon={<VpnKeyIcon />}
                                sx={{ my: 1 }}
                                data-testid={`repo-error-${repo.name}`}
                              >
                                <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                                  Clone failed
                                </Typography>
                                {repo.errorMessage && (
                                  <Typography variant="body2" sx={{ mb: 1, fontFamily: 'monospace', whiteSpace: 'pre-wrap', fontSize: '0.8rem' }}>
                                    {repo.errorMessage}
                                  </Typography>
                                )}
                                <Typography variant="body2">
                                  {isSshUrl
                                    ? 'This is a private repository using SSH. You must add the VM\u2019s deploy key to this repository on GitHub.'
                                    : 'This repository may be private. Ensure the GitHub PAT has access, or switch to an SSH URL and add the VM\u2019s deploy key.'}
                                </Typography>
                                <Typography variant="body2" sx={{ mt: 0.5 }}>
                                  <strong>Steps:</strong> Go to your repository Settings &rarr; Deploy keys &rarr; Add deploy key, then paste the public key below.
                                </Typography>
                                {repo.deployPublicKey ? (
                                  <Box sx={{ mt: 1, position: 'relative' }}>
                                    <Typography variant="caption" fontWeight={700}>Public key:</Typography>
                                    <Box
                                      sx={{
                                        mt: 0.5,
                                        p: 1,
                                        pr: 5,
                                        bgcolor: 'grey.100',
                                        borderRadius: 1,
                                        fontFamily: 'monospace',
                                        fontSize: '0.75rem',
                                        wordBreak: 'break-all',
                                        whiteSpace: 'pre-wrap',
                                      }}
                                    >
                                      {repo.deployPublicKey}
                                      <Tooltip title={copiedKey ? 'Copied!' : 'Copy to clipboard'}>
                                        <IconButton
                                          size="small"
                                          sx={{ position: 'absolute', top: 24, right: 4 }}
                                          onClick={(e) => {
                                            e.stopPropagation()
                                            navigator.clipboard.writeText(repo.deployPublicKey!)
                                            setCopiedKey(true)
                                            setTimeout(() => setCopiedKey(false), 2000)
                                          }}
                                          data-testid="btn-copy-deploy-key"
                                        >
                                          <ContentCopyIcon fontSize="small" />
                                        </IconButton>
                                      </Tooltip>
                                    </Box>
                                  </Box>
                                ) : null}
                                <Button
                                  variant="outlined"
                                  size="small"
                                  startIcon={<RefreshIcon />}
                                  sx={{ mt: 1.5 }}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    retryClone({ variables: { name: repo.name } })
                                  }}
                                  data-testid={`btn-retry-clone-${repo.name}`}
                                >
                                  Retry Clone
                                </Button>
                              </Alert>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  )
                })}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Add Repository dialog */}
      <Dialog open={dialogOpen} onClose={handleClose} maxWidth="sm" fullWidth>
        <DialogTitle>Add Repository</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {formError && <Alert severity="error">{formError}</Alert>}
            {isGithubAuthenticated ? (
              <Autocomplete
                freeSolo
                options={githubRepos}
                loading={ghReposLoading}
                getOptionLabel={(option) =>
                  typeof option === 'string' ? option : option.nameWithOwner
                }
                filterOptions={(options, { inputValue }) => {
                  const q = inputValue.toLowerCase()
                  return options.filter(
                    (o) =>
                      o.nameWithOwner.toLowerCase().includes(q) ||
                      (o.description?.toLowerCase().includes(q) ?? false)
                  )
                }}
                renderOption={(props, option) => (
                  <li {...props} key={option.nameWithOwner}>
                    <Stack sx={{ width: '100%' }}>
                      <Stack direction="row" alignItems="center" spacing={0.5}>
                        <Typography variant="body2" fontWeight={600}>
                          {option.nameWithOwner}
                        </Typography>
                        {option.isPrivate && (
                          <LockIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                        )}
                      </Stack>
                      {option.description && (
                        <Typography variant="caption" color="text.secondary" noWrap>
                          {option.description}
                        </Typography>
                      )}
                    </Stack>
                  </li>
                )}
                onChange={(_e, value) => {
                  if (!value) {
                    setForm((f) => ({ ...f, name: '', url: '', defaultBranch: null }))
                  } else if (typeof value !== 'string') {
                    const repoName = value.nameWithOwner.split('/').pop() ?? value.nameWithOwner
                    setForm((f) => ({
                      ...f,
                      name: repoName,
                      url: value.url,
                      defaultBranch: value.defaultBranch,
                    }))
                  }
                }}
                onInputChange={(_e, value, reason) => {
                  if (reason === 'input') {
                    setForm((f) => ({ ...f, name: value }))
                  }
                }}
                inputValue={form.name}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Repository"
                    required
                    placeholder="Search your GitHub repos or type a name"
                    data-testid="repo-form-name"
                  />
                )}
                data-testid="repo-form-autocomplete"
              />
            ) : (
              <TextField
                label="Name"
                required
                fullWidth
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                data-testid="repo-form-name"
              />
            )}
            <TextField
              label="URL"
              required
              fullWidth
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="https://github.com/org/repo.git"
              data-testid="repo-form-url"
              slotProps={{
                input: {
                  endAdornment: form.url ? (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        onClick={() => setForm((f) => ({ ...f, url: '' }))}
                        edge="end"
                      >
                        <ClearIcon fontSize="small" />
                      </IconButton>
                    </InputAdornment>
                  ) : undefined,
                },
              }}
            />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Pollers</Typography>
              <FormGroup row>
                {AVAILABLE_POLLERS.map(({ value, label }) => (
                  <FormControlLabel
                    key={value}
                    control={
                      <Checkbox
                        checked={form.pollers.includes(value)}
                        onChange={(e) => {
                          setForm((f) => ({
                            ...f,
                            pollers: e.target.checked
                              ? [...f.pollers, value]
                              : f.pollers.filter((p) => p !== value),
                          }))
                        }}
                        data-testid={`repo-form-poller-${value}`}
                      />
                    }
                    label={label}
                  />
                ))}
              </FormGroup>
            </Box>
            <FormControlLabel
              control={
                <Switch
                  checked={form.isConfigRepo}
                  onChange={(e) => setForm((f) => ({ ...f, isConfigRepo: e.target.checked }))}
                  data-testid="repo-form-is-config-repo"
                />
              }
              label="Use as global config repository"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={registering}
            data-testid="btn-add-repository-confirm"
          >
            {registering ? 'Adding…' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbarMsg !== null}
        autoHideDuration={6000}
        onClose={() => setSnackbarMsg(null)}
        message={snackbarMsg}
      />

      {/* GitHub Login dialog */}
      <Dialog open={loginDialogOpen} onClose={handleLoginDialogClose} maxWidth="xs" fullWidth>
        <DialogTitle>
          <Stack direction="row" alignItems="center" spacing={1}>
            <GitHubIcon />
            <span>GitHub Login</span>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }} alignItems="center">
            {loginError && <Alert severity="error" sx={{ width: '100%' }}>{loginError}</Alert>}
            {loginSuccess && (
              <Alert severity="success" icon={<CheckCircleIcon />} sx={{ width: '100%' }}>
                {loginSuccess}
              </Alert>
            )}
            {deviceCode && (
              <>
                <Typography variant="body2" align="center">
                  Enter this code on GitHub:
                </Typography>
                <Box
                  sx={{
                    px: 3,
                    py: 1.5,
                    bgcolor: 'grey.100',
                    borderRadius: 1,
                    fontFamily: 'monospace',
                    fontSize: '1.5rem',
                    fontWeight: 700,
                    letterSpacing: '0.15em',
                    userSelect: 'all',
                    textAlign: 'center',
                  }}
                >
                  {deviceCode.userCode}
                </Box>
                <Button
                  variant="contained"
                  startIcon={<GitHubIcon />}
                  href={deviceCode.verificationUri}
                  target="_blank"
                  rel="noopener"
                  onClick={() => {
                    navigator.clipboard.writeText(deviceCode.userCode)
                    setCopiedCode(true)
                    setTimeout(() => setCopiedCode(false), 2000)
                    setGithubOpened(true)
                  }}
                >
                  {copiedCode ? 'Copied! Opening GitHub…' : 'Copy Code & Open GitHub'}
                </Button>
                {githubOpened && (
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <CircularProgress size={16} />
                    <Typography variant="body2" color="text.secondary">
                      Waiting for authorization...
                    </Typography>
                  </Stack>
                )}
              </>
            )}
            {!deviceCode && !loginError && !loginSuccess && (
              <CircularProgress />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleLoginDialogClose}>
            {loginSuccess ? 'Done' : 'Cancel'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
