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
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import InputLabel from '@mui/material/InputLabel'
import FormControl from '@mui/material/FormControl'
import Divider from '@mui/material/Divider'
import { GET_REPOSITORIES, REGISTER_REPOSITORY, REMOVE_REPOSITORY, RETRY_CLONE, GITHUB_AUTH_STATUS, GITHUB_LOGIN_START, GITHUB_LOGIN_POLL, GITHUB_LOGOUT, GITHUB_REPOSITORIES, GITHUB_BRANCHES, UPDATE_REPOSITORY } from '@/lib/graphql/queries'
import { GET_PIPELINE_DEFINITIONS } from '@/lib/graphql/agent-queries'
import { formatDate } from '@/lib/format'

interface GitFlowBranches {
  stable: string
  development: string
  release: string
  feature: string
  bugfix: string
  hotfix: string
}

interface BranchRule {
  issueLabels: string[]
  baseBranch: string
  pipeline: string | null
}

interface GitFlowBranchRules {
  feature: BranchRule | null
  bugfix: BranchRule | null
  hotfix: BranchRule | null
  branchNameOverride: string | null
}

interface GitFlowConfig {
  enabled: boolean
  branches: GitFlowBranches
  rules: GitFlowBranchRules | null
}

interface BranchRuleFormState {
  issueLabels: string
  baseBranch: 'stable' | 'release' | 'development'
  pipeline: string
}

interface GitFlowRulesFormState {
  feature: BranchRuleFormState
  bugfix: BranchRuleFormState
  hotfix: BranchRuleFormState
  branchNameOverride: string
}

interface Repository {
  name: string
  url: string
  branch: string
  cloneDir: string
  pollers: string[]
  cloneStatus: string
  lastPulledAt: string | null
  errorMessage: string | null
  deployPublicKey: string | null
  taskCount: number
  hasClaudeAgents: boolean
  gitFlowConfig: GitFlowConfig | null
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

const DEFAULT_GIT_FLOW_BRANCHES: GitFlowBranches = {
  stable: 'main',
  development: 'develop',
  release: 'release/*',
  feature: 'feature/*',
  bugfix: 'bugfix/*',
  hotfix: 'hotfix/*',
}

const DEFAULT_GIT_FLOW_RULES: GitFlowRulesFormState = {
  feature: { issueLabels: 'feature, enhancement', baseBranch: 'development', pipeline: '' },
  bugfix:  { issueLabels: 'bug',                  baseBranch: 'release',     pipeline: '' },
  hotfix:  { issueLabels: 'hotfix',               baseBranch: 'stable',      pipeline: '' },
  branchNameOverride: 'base:*',
}

function findDefaultPipeline(pipelineNames: string[], branchPrefix: string, issueLabels: string): string {
  const lower = (s: string) => s.toLowerCase()
  const byBranch = pipelineNames.find(name => lower(name).includes(lower(branchPrefix)))
  if (byBranch) return byBranch
  const labels = issueLabels.split(',').map(s => s.trim()).filter(Boolean)
  for (const label of labels) {
    const byLabel = pipelineNames.find(name => lower(name).includes(lower(label)))
    if (byLabel) return byLabel
  }
  return ''
}

// Shared form state for both Add and Edit dialogs
interface RepoFormState {
  url: string
  branch: string
  pollers: string[]
  gitFlowEnabled: boolean
  gitFlowBranches: GitFlowBranches
  gitFlowRules: GitFlowRulesFormState
}

interface AddRepoFormState extends RepoFormState {
  name: string
}

const EMPTY_FORM: AddRepoFormState = {
  name: '',
  url: '',
  branch: '',
  pollers: [...DEFAULT_POLLERS],
  gitFlowEnabled: false,
  gitFlowBranches: { ...DEFAULT_GIT_FLOW_BRANCHES },
  gitFlowRules: { ...DEFAULT_GIT_FLOW_RULES },
}

function parseGithubOwnerRepo(url: string): { owner: string; repo: string } | null {
  const m = url.match(/github\.com[/:]([^/]+)\/([^/.\s]+?)(?:\.git)?(?:\/.*)?$/)
  return m ? { owner: m[1], repo: m[2] } : null
}

interface RepoSettingsFieldsProps {
  value: RepoFormState
  onChange: (patch: Partial<RepoFormState>) => void
  branches: string[]
  branchesLoading: boolean
  pipelines: string[]
  showUrlClear?: boolean
  testIdPrefix?: string
}

function buildRuleDescription(
  branchPattern: string,
  rule: BranchRuleFormState,
  gitFlowBranches: GitFlowBranches,
  gitFlowEnabled: boolean
): string {
  const labels = rule.issueLabels.split(',').map(s => s.trim()).filter(Boolean)
  const labelText = labels.length > 0 ? labels.map(l => `"${l}"`).join(' or ') : '<no labels set>'
  const branchPrefix = branchPattern.replace(/\/\*$/, '')
  const pipelineText = rule.pipeline || '<no pipeline set>'
  if (!gitFlowEnabled) {
    return `When issue is labeled ${labelText}, branch ${branchPrefix}/<issue_id>-<issue-name> is created and ${pipelineText} pipeline is applied`
  }
  const baseBranchName =
    rule.baseBranch === 'stable' ? (gitFlowBranches.stable || 'main')
    : rule.baseBranch === 'release' ? 'Active release branch'
    : (gitFlowBranches.development || 'develop')
  return `When issue is labeled ${labelText}, branch ${branchPrefix}/<issue_id>-<issue-name> is created from ${baseBranchName} and ${pipelineText} pipeline is applied which creates a PR ${branchPrefix}/* → ${baseBranchName}`
}

function RepoSettingsFields({
  value,
  onChange,
  branches,
  branchesLoading,
  pipelines,
  showUrlClear = false,
  testIdPrefix = '',
}: RepoSettingsFieldsProps) {
  return (
    <Stack spacing={2}>
      <TextField
        label="URL"
        required
        fullWidth
        value={value.url}
        onChange={(e) => onChange({ url: e.target.value })}
        placeholder="https://github.com/org/repo.git"
        data-testid={`${testIdPrefix}url`}
        InputProps={{
          endAdornment: showUrlClear && value.url ? (
            <InputAdornment position="end">
              <IconButton size="small" onClick={() => onChange({ url: '' })} edge="end">
                <ClearIcon fontSize="small" />
              </IconButton>
            </InputAdornment>
          ) : undefined,
        }}
      />
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Pollers</Typography>
        <FormGroup row>
          {AVAILABLE_POLLERS.map(({ value: pv, label }) => (
            <FormControlLabel
              key={pv}
              control={
                <Checkbox
                  checked={value.pollers.includes(pv)}
                  onChange={(e) =>
                    onChange({
                      pollers: e.target.checked
                        ? [...value.pollers, pv]
                        : value.pollers.filter((p) => p !== pv),
                    })
                  }
                  data-testid={`${testIdPrefix}poller-${pv}`}
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
            checked={value.gitFlowEnabled}
            onChange={(e) => onChange({ gitFlowEnabled: e.target.checked })}
            data-testid={`${testIdPrefix}git-flow-enabled`}
          />
        }
        label="Enable Git Flow"
      />
      {value.gitFlowEnabled ? (
        <Stack spacing={1.5}>
          <Typography variant="caption" color="text.secondary">
            Branch name patterns (use <code>*</code> as a wildcard)
          </Typography>
          <Autocomplete
            freeSolo
            options={branches}
            loading={branchesLoading}
            value={value.gitFlowBranches.stable}
            onChange={(_e, v) => onChange({ gitFlowBranches: { ...value.gitFlowBranches, stable: v ?? '' } })}
            onInputChange={(_e, v, reason) => { if (reason === 'input') onChange({ gitFlowBranches: { ...value.gitFlowBranches, stable: v } }) }}
            renderInput={(params) => (
              <TextField {...params} label="Stable" data-testid={`${testIdPrefix}git-flow-branch-stable`} />
            )}
          />
          <TextField
            label="Release"
            fullWidth
            value={value.gitFlowBranches.release}
            onChange={(e) => onChange({ gitFlowBranches: { ...value.gitFlowBranches, release: e.target.value } })}
            data-testid={`${testIdPrefix}git-flow-branch-release`}
            helperText="The active release branch is the one ahead of the stable branch"
          />
          <Autocomplete
            freeSolo
            options={branches}
            loading={branchesLoading}
            value={value.gitFlowBranches.development}
            onChange={(_e, v) => onChange({ gitFlowBranches: { ...value.gitFlowBranches, development: v ?? '' } })}
            onInputChange={(_e, v, reason) => { if (reason === 'input') onChange({ gitFlowBranches: { ...value.gitFlowBranches, development: v } }) }}
            renderInput={(params) => (
              <TextField {...params} label="Development" data-testid={`${testIdPrefix}git-flow-branch-development`} />
            )}
          />
        </Stack>
      ) : (
        <Autocomplete
          freeSolo
          options={branches}
          loading={branchesLoading}
          value={value.branch}
          onChange={(_e, v) => onChange({ branch: v ?? '' })}
          onInputChange={(_e, v, reason) => { if (reason === 'input') onChange({ branch: v }) }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Branch"
              placeholder="default"
              helperText="Used as the single working branch (equivalent to main + develop in Git Flow)"
              data-testid={`${testIdPrefix}branch`}
            />
          )}
        />
      )}
      <Box>
        <Divider sx={{ mb: 1.5 }}>
          <Typography variant="caption" color="text.secondary" fontWeight={600} sx={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Rules
          </Typography>
        </Divider>
        <Stack spacing={2}>
          {(['feature', 'bugfix', 'hotfix'] as const).map((field) => {
            const rule = value.gitFlowRules[field]
            const branchPattern = value.gitFlowBranches[field]
            return (
              <Box key={field}>
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  <TextField
                    label="Issue labels"
                    size="small"
                    sx={{ flex: 2 }}
                    value={rule.issueLabels}
                    onChange={(e) =>
                      onChange({
                        gitFlowRules: {
                          ...value.gitFlowRules,
                          [field]: { ...rule, issueLabels: e.target.value },
                        },
                      })
                    }
                    placeholder="e.g. feature, enhancement"
                    helperText="Comma-separated"
                    data-testid={`${testIdPrefix}git-flow-rule-${field}-labels`}
                  />
                  <TextField
                    label={field.charAt(0).toUpperCase() + field.slice(1)}
                    size="small"
                    sx={{ flex: 1.5 }}
                    value={value.gitFlowBranches[field]}
                    onChange={(e) =>
                      onChange({ gitFlowBranches: { ...value.gitFlowBranches, [field]: e.target.value } })
                    }
                    data-testid={`${testIdPrefix}git-flow-branch-${field}`}
                  />
                  {value.gitFlowEnabled && (
                    <FormControl size="small" sx={{ flex: 1, minWidth: 120 }}>
                      <InputLabel>Base branch</InputLabel>
                      <Select
                        value={rule.baseBranch}
                        label="Base branch"
                        onChange={(e) =>
                          onChange({
                            gitFlowRules: {
                              ...value.gitFlowRules,
                              [field]: { ...rule, baseBranch: e.target.value as BranchRuleFormState['baseBranch'] },
                            },
                          })
                        }
                        data-testid={`${testIdPrefix}git-flow-rule-${field}-base`}
                      >
                        <MenuItem value="stable">Stable</MenuItem>
                        <MenuItem value="release">Release</MenuItem>
                        <MenuItem value="development">Development</MenuItem>
                      </Select>
                    </FormControl>
                  )}
                  <FormControl size="small" sx={{ flex: 1.5, minWidth: 130 }}>
                    <InputLabel>Pipeline</InputLabel>
                    <Select
                      value={rule.pipeline}
                      label="Pipeline"
                      renderValue={(v) => v || <em>None</em>}
                      onChange={(e) =>
                        onChange({
                          gitFlowRules: {
                            ...value.gitFlowRules,
                            [field]: { ...rule, pipeline: e.target.value },
                          },
                        })
                      }
                      data-testid={`${testIdPrefix}git-flow-rule-${field}-pipeline`}
                    >
                      <MenuItem value=""><em>None</em></MenuItem>
                      {pipelines.map((p) => (
                        <MenuItem key={p} value={p}>{p}</MenuItem>
                      ))}
                      {rule.pipeline && !pipelines.includes(rule.pipeline) && (
                        <MenuItem value={rule.pipeline}>{rule.pipeline}</MenuItem>
                      )}
                    </Select>
                  </FormControl>
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                  {buildRuleDescription(branchPattern, rule, value.gitFlowBranches, value.gitFlowEnabled)}
                </Typography>
              </Box>
            )
          })}
          <Box>
            <TextField
              label="Branch name override"
              fullWidth
              size="small"
              value={value.gitFlowRules.branchNameOverride}
              onChange={(e) =>
                onChange({ gitFlowRules: { ...value.gitFlowRules, branchNameOverride: e.target.value } })
              }
              data-testid={`${testIdPrefix}git-flow-branch-name-override`}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              If an issue has a label matching this pattern, it overrides the base branch in the above rules (e.g. <code>base:main</code> overrides to <code>main</code>)
            </Typography>
          </Box>
        </Stack>
      </Box>
    </Stack>
  )
}

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

  // Edit repository dialog — declared early so its URL can drive the branch query below
  const [editDialogRepo, setEditDialogRepo] = useState<Repository | null>(null)
  const [editForm, setEditForm] = useState<RepoFormState>(
    { url: '', branch: '', pollers: [], gitFlowEnabled: false, gitFlowBranches: { ...DEFAULT_GIT_FLOW_BRANCHES }, gitFlowRules: { ...DEFAULT_GIT_FLOW_RULES } }
  )
  const [editError, setEditError] = useState<string | null>(null)

  const { data, loading, error, refetch } = useQuery(GET_REPOSITORIES)
  const { data: authData, loading: authLoading, refetch: refetchAuth } = useQuery(GITHUB_AUTH_STATUS)
  const { data: pipelineDefsData } = useQuery(GET_PIPELINE_DEFINITIONS)
  const pipelineNames: string[] = (pipelineDefsData?.pipelineDefinitions ?? []).map((p: { name: string }) => p.name)
  const isGithubAuthenticated = authData?.githubAuthStatus?.authenticated === true
  const { data: ghReposData, loading: ghReposLoading } = useQuery(GITHUB_REPOSITORIES, {
    skip: !isGithubAuthenticated,
  })
  const githubRepos: GithubRepo[] = ghReposData?.githubRepositories ?? []

  const addFormParsed = parseGithubOwnerRepo(form.url)
  const { data: addBranchesData, loading: addBranchesLoading } = useQuery(GITHUB_BRANCHES, {
    skip: !isGithubAuthenticated || !addFormParsed,
    variables: addFormParsed ?? { owner: '', repo: '' },
  })
  const addBranches: string[] = addBranchesData?.githubBranches ?? []

  const editParsed = parseGithubOwnerRepo(editDialogRepo?.url ?? '')
  const { data: editBranchesData, loading: editBranchesLoading } = useQuery(GITHUB_BRANCHES, {
    skip: !isGithubAuthenticated || !editParsed || !editDialogRepo,
    variables: editParsed ?? { owner: '', repo: '' },
  })
  const editBranches: string[] = editBranchesData?.githubBranches ?? []

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

  const [retryClone] = useMutation(RETRY_CLONE, {
    onCompleted: () => refetch(),
  })

  const [updateRepository, { loading: savingEdit }] = useMutation(UPDATE_REPOSITORY, {
    onCompleted: (result) => {
      const errors = result?.updateRepository?.errors
      if (errors?.length) {
        setEditError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setEditDialogRepo(null)
        setEditError(null)
        refetch()
      }
    },
    onError: (err) => setEditError(err.message),
  })

  function ruleToFormState(rule: BranchRule | null | undefined, field: 'feature' | 'bugfix' | 'hotfix', branchPattern: string): BranchRuleFormState {
    const defaults = DEFAULT_GIT_FLOW_RULES[field]
    const branchPrefix = branchPattern.replace(/\/\*$/, '')
    const defaultPipeline = findDefaultPipeline(pipelineNames, branchPrefix, defaults.issueLabels)
    if (!rule) return { ...defaults, pipeline: defaultPipeline }
    return {
      issueLabels: rule.issueLabels.join(', ') || defaults.issueLabels,
      baseBranch: (rule.baseBranch as BranchRuleFormState['baseBranch']) || defaults.baseBranch,
      pipeline: rule.pipeline ?? defaultPipeline,
    }
  }

  function openEditDialog(repo: Repository) {
    setEditDialogRepo(repo)
    setEditError(null)
    const rules = repo.gitFlowConfig?.rules
    setEditForm({
      url: repo.url,
      branch: repo.branch ?? '',
      pollers: [...(repo.pollers ?? [])],
      gitFlowEnabled: repo.gitFlowConfig?.enabled ?? false,
      gitFlowBranches: repo.gitFlowConfig
        ? {
            stable: repo.gitFlowConfig.branches.stable,
            development: repo.gitFlowConfig.branches.development,
            release: repo.gitFlowConfig.branches.release,
            feature: repo.gitFlowConfig.branches.feature,
            bugfix: repo.gitFlowConfig.branches.bugfix,
            hotfix: repo.gitFlowConfig.branches.hotfix,
          }
        : { ...DEFAULT_GIT_FLOW_BRANCHES },
      gitFlowRules: {
        feature: ruleToFormState(rules?.feature, 'feature', repo.gitFlowConfig?.branches?.feature ?? DEFAULT_GIT_FLOW_BRANCHES.feature),
        bugfix:  ruleToFormState(rules?.bugfix,  'bugfix',  repo.gitFlowConfig?.branches?.bugfix  ?? DEFAULT_GIT_FLOW_BRANCHES.bugfix),
        hotfix:  ruleToFormState(rules?.hotfix,  'hotfix',  repo.gitFlowConfig?.branches?.hotfix  ?? DEFAULT_GIT_FLOW_BRANCHES.hotfix),
        branchNameOverride: rules?.branchNameOverride ?? 'base:*',
      },
    })
  }

  function ruleFormToInput(rule: BranchRuleFormState) {
    return {
      issueLabels: rule.issueLabels.split(',').map(s => s.trim()).filter(Boolean),
      baseBranch: rule.baseBranch,
      pipeline: rule.pipeline || null,
    }
  }

  function handleEditSave() {
    if (!editDialogRepo) return
    updateRepository({
      variables: {
        name: editDialogRepo.name,
        input: {
          url: editForm.url,
          branch: editForm.branch || null,
          pollers: editForm.pollers,
          gitFlowConfig: {
              enabled: editForm.gitFlowEnabled,
              branches: editForm.gitFlowBranches,
              rules: {
                feature: ruleFormToInput(editForm.gitFlowRules.feature),
                bugfix: ruleFormToInput(editForm.gitFlowRules.bugfix),
                hotfix: ruleFormToInput(editForm.gitFlowRules.hotfix),
                branchNameOverride: editForm.gitFlowRules.branchNameOverride || null,
              },
            },
        },
      },
    })
  }


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
          branch: form.branch || undefined,
          pollers: form.pollers,
          gitFlowConfig: {
              enabled: form.gitFlowEnabled,
              branches: form.gitFlowBranches,
              rules: {
                feature: ruleFormToInput(form.gitFlowRules.feature),
                bugfix: ruleFormToInput(form.gitFlowRules.bugfix),
                hotfix: ruleFormToInput(form.gitFlowRules.hotfix),
                branchNameOverride: form.gitFlowRules.branchNameOverride || null,
              },
            },
        },
      },
    })
  }

  function openAddDialog() {
    setForm({
      ...EMPTY_FORM,
      gitFlowRules: {
        ...DEFAULT_GIT_FLOW_RULES,
        feature: { ...DEFAULT_GIT_FLOW_RULES.feature, pipeline: findDefaultPipeline(pipelineNames, DEFAULT_GIT_FLOW_BRANCHES.feature.replace(/\/\*$/, ''), DEFAULT_GIT_FLOW_RULES.feature.issueLabels) },
        bugfix:  { ...DEFAULT_GIT_FLOW_RULES.bugfix,  pipeline: findDefaultPipeline(pipelineNames, DEFAULT_GIT_FLOW_BRANCHES.bugfix.replace(/\/\*$/, ''),  DEFAULT_GIT_FLOW_RULES.bugfix.issueLabels)  },
        hotfix:  { ...DEFAULT_GIT_FLOW_RULES.hotfix,  pipeline: findDefaultPipeline(pipelineNames, DEFAULT_GIT_FLOW_BRANCHES.hotfix.replace(/\/\*$/, ''),  DEFAULT_GIT_FLOW_RULES.hotfix.issueLabels)  },
      },
    })
    setFormError(null)
    setDialogOpen(true)
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
            onClick={openAddDialog}
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
                        <TableCell>{formatDate(repo.lastPulledAt)}</TableCell>
                        <TableCell align="right">{repo.taskCount ?? 0}</TableCell>
                        <TableCell align="right" sx={{ py: 0 }}>
                          <Stack direction="row" spacing={0} justifyContent="flex-end">
                            <Tooltip title="Repository settings">
                              <IconButton
                                size="small"
                                onClick={(e) => { e.stopPropagation(); openEditDialog(repo) }}
                                data-testid={`btn-edit-repo-${repo.name}`}
                                color={repo.gitFlowConfig?.enabled ? 'primary' : 'default'}
                              >
                                <SettingsIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
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
                    setForm((f) => ({ ...f, name: '', url: '', branch: '' }))
                  } else if (typeof value !== 'string') {
                    const repoName = value.nameWithOwner.split('/').pop() ?? value.nameWithOwner
                    setForm((f) => ({
                      ...f,
                      name: repoName,
                      url: value.url,
                      branch: value.defaultBranch,
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
            <RepoSettingsFields
              value={form}
              onChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
              branches={addBranches}
              branchesLoading={addBranchesLoading}
              pipelines={pipelineNames}
              showUrlClear
              testIdPrefix="repo-form-"
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

      {/* Edit Repository dialog */}
      <Dialog open={editDialogRepo !== null} onClose={() => setEditDialogRepo(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Settings — {editDialogRepo?.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {editError && <Alert severity="error">{editError}</Alert>}
            <RepoSettingsFields
              value={editForm}
              onChange={(patch) => setEditForm((f) => ({ ...f, ...patch }))}
              branches={editBranches}
              branchesLoading={editBranchesLoading}
              pipelines={pipelineNames}
              testIdPrefix="edit-repo-"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditDialogRepo(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleEditSave}
            disabled={savingEdit}
            data-testid="btn-edit-repo-save"
          >
            {savingEdit ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

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
