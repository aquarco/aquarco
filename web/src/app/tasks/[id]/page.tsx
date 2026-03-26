'use client'

import { useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import { useParams } from 'next/navigation'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Grid from '@mui/material/Grid'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import Stack from '@mui/material/Stack'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Stepper from '@mui/material/Stepper'
import Step from '@mui/material/Step'
import StepLabel from '@mui/material/StepLabel'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import LoopIcon from '@mui/icons-material/Loop'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import RepeatIcon from '@mui/icons-material/Repeat'
import Tooltip from '@mui/material/Tooltip'
import Divider from '@mui/material/Divider'
import { GET_TASK, GET_PIPELINE_DEFINITIONS, RETRY_TASK, RERUN_TASK, CLOSE_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'

interface Stage {
  id: string
  stageNumber: number
  category: string
  agent: string | null
  agentVersion: string | null
  status: string
  startedAt: string | null
  completedAt: string | null
  structuredOutput: unknown | null
  rawOutput: string | null
  tokensInput: number | null
  tokensOutput: number | null
  errorMessage: string | null
  retryCount: number
  liveOutput: string | null
}

interface ContextEntry {
  id: string
  key: string
  valueType: string
  valueJson: unknown | null
  valueText: string | null
  valueFileRef: string | null
  createdAt: string
  stageNumber: number | null
}

interface PipelineLoopConfig {
  condition: string
  maxRepeats: number
  evalMode: string
  loopStages: string[]
}

interface PipelineStageDefinition {
  category: string
  required: boolean
  conditions: string[]
  loop: PipelineLoopConfig | null
}

interface PipelineDefinition {
  name: string
  version: string
  triggerLabels: string[]
  triggerEvents: string[]
  stages: PipelineStageDefinition[]
}

interface Task {
  id: string
  title: string
  status: string
  priority: number
  source: string
  sourceRef: string | null
  pipeline: string
  repository: { name: string }
  createdAt: string
  updatedAt: string
  startedAt: string | null
  completedAt: string | null
  assignedAgent: string | null
  currentStage: number
  retryCount: number
  errorMessage: string | null
  parentTaskId: string | null
  prNumber: number | null
  branchName: string | null
  stages: Stage[]
  context: ContextEntry[]
}

function resolveContextValue(entry: ContextEntry): { display: string; isJson: boolean } {
  if (entry.valueJson != null) {
    return { display: JSON.stringify(entry.valueJson, null, 2), isJson: true }
  }
  if (entry.valueText != null) {
    return { display: entry.valueText, isJson: false }
  }
  if (entry.valueFileRef != null) {
    return { display: entry.valueFileRef, isJson: false }
  }
  return { display: '—', isJson: false }
}

export default function TaskDetailPage() {
  const params = useParams()
  const id = params?.id as string

  const [unblockOpen, setUnblockOpen] = useState(false)
  const [resolution, setResolution] = useState('')
  const [mutationError, setMutationError] = useState<string | null>(null)

  const { data, loading, error, refetch } = useQuery(GET_TASK, {
    variables: { id },
    skip: !id,
    pollInterval: 5000,
  })

  const { data: pipelineData } = useQuery(GET_PIPELINE_DEFINITIONS)

  const [retryTask, { loading: retrying }] = useMutation(RETRY_TASK, {
    variables: { id },
    onCompleted: (result) => {
      const errors = result?.retryTask?.errors
      if (errors?.length) {
        setMutationError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setMutationError(null)
        refetch()
      }
    },
  })

  const [rerunTask, { loading: rerunning }] = useMutation(RERUN_TASK, {
    variables: { id },
    onCompleted: (result) => {
      const errors = result?.rerunTask?.errors
      if (errors?.length) {
        setMutationError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setMutationError(null)
        refetch()
      }
    },
  })

  const [closeTask, { loading: closing }] = useMutation(CLOSE_TASK, {
    variables: { id },
    onCompleted: (result) => {
      const errors = result?.closeTask?.errors
      if (errors?.length) {
        setMutationError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setMutationError(null)
        refetch()
      }
    },
  })

  const [cancelTask, { loading: cancelling }] = useMutation(CANCEL_TASK, {
    variables: { id },
    onCompleted: (result) => {
      const errors = result?.cancelTask?.errors
      if (errors?.length) {
        setMutationError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setMutationError(null)
        refetch()
      }
    },
  })

  const [unblockTask, { loading: unblocking }] = useMutation(UNBLOCK_TASK, {
    onCompleted: (result) => {
      const errors = result?.unblockTask?.errors
      if (errors?.length) {
        setMutationError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        setMutationError(null)
        setUnblockOpen(false)
        setResolution('')
        refetch()
      }
    },
  })

  if (loading) {
    return (
      <Box>
        <Skeleton variant="text" width={300} height={40} />
        <Skeleton variant="rectangular" height={200} sx={{ mt: 2 }} />
        <Skeleton variant="rectangular" height={120} sx={{ mt: 2 }} />
      </Box>
    )
  }

  if (error) {
    return (
      <Alert severity="error">Failed to load task: {error.message}</Alert>
    )
  }

  const task: Task | undefined = data?.task

  if (!task) {
    return <Alert severity="warning">Task not found.</Alert>
  }

  const status = task.status?.toUpperCase()
  const canRetry = status === 'FAILED' || status === 'RATE_LIMITED' || status === 'TIMEOUT'
  const canRerun = status === 'COMPLETED' || status === 'FAILED' || status === 'CLOSED'
  const canClose = status === 'COMPLETED'
  const canCancel = status === 'PENDING' || status === 'QUEUED' || status === 'EXECUTING'
  const canUnblock = status === 'BLOCKED'

  const stages = task.stages.slice().sort((a, b) => a.stageNumber - b.stageNumber)
  const activeStep = stages.findIndex(
    (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
  )

  // Group stages by stageNumber to detect loop iterations
  // Stages with the same stageNumber + category but different retryCount are loop iterations
  const stagesByNumber = new Map<number, Stage[]>()
  for (const stage of stages) {
    const key = stage.stageNumber
    if (!stagesByNumber.has(key)) {
      stagesByNumber.set(key, [])
    }
    stagesByNumber.get(key)!.push(stage)
  }

  // Deduplicated stages for the stepper (one per stageNumber)
  const uniqueStages = Array.from(stagesByNumber.entries())
    .sort(([a], [b]) => a - b)
    .map(([, group]) => {
      // Pick the latest iteration as the "representative" stage
      const latest = group[group.length - 1]
      return { ...latest, iterationCount: group.length }
    })

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h5" fontWeight={700}>
            {task.title}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={monoStyle}>
            {task.id}
          </Typography>
        </Box>
        <StatusChip status={task.status} size="medium" />
      </Stack>

      {mutationError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setMutationError(null)}>
          {mutationError}
        </Alert>
      )}

      {/* Metadata card */}
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Source</Typography>
              <Typography variant="body2">{task.source}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Repository</Typography>
              <Typography variant="body2">{task.repository.name}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Pipeline</Typography>
              <Typography variant="body2">{task.pipeline}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Assigned Agent</Typography>
              <Typography variant="body2">{task.assignedAgent ?? '—'}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Priority</Typography>
              <Typography variant="body2">{task.priority}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Current Stage</Typography>
              <Typography variant="body2">{task.currentStage}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">Retry Count</Typography>
              <Typography variant="body2">{task.retryCount}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <Typography variant="caption" color="text.secondary">Created</Typography>
              <Typography variant="body2">{formatDate(task.createdAt)}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <Typography variant="caption" color="text.secondary">Started</Typography>
              <Typography variant="body2">{formatDate(task.startedAt)}</Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <Typography variant="caption" color="text.secondary">Completed</Typography>
              <Typography variant="body2">{formatDate(task.completedAt)}</Typography>
            </Grid>
            {task.errorMessage && (
              <Grid item xs={12}>
                <Typography variant="caption" color="text.secondary">Error</Typography>
                <Typography variant="body2" color="error.main">{task.errorMessage}</Typography>
              </Grid>
            )}
          </Grid>
        </CardContent>
      </Card>

      {/* Pipeline stepper */}
      {stages.length > 0 && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Pipeline Stages
            </Typography>
            <Stepper activeStep={activeStep} orientation="horizontal" alternativeLabel>
              {uniqueStages.map((stage) => (
                <Step
                  key={stage.id}
                  completed={stage.status === 'COMPLETED'}
                >
                  <StepLabel
                    error={stage.status === 'FAILED'}
                    optional={
                      <Box textAlign="center">
                        {stage.agent && (
                          <Typography variant="caption" color="text.secondary" display="block">
                            {stage.agent}
                          </Typography>
                        )}
                        {stage.iterationCount > 1 && (
                          <Tooltip title={`${stage.iterationCount} iterations (loop)`}>
                            <Chip
                              icon={<LoopIcon sx={{ fontSize: 14 }} />}
                              label={`x${stage.iterationCount}`}
                              size="small"
                              variant="outlined"
                              color="info"
                              sx={{ mt: 0.5, height: 20, '& .MuiChip-label': { px: 0.5, fontSize: '0.7rem' } }}
                            />
                          </Tooltip>
                        )}
                      </Box>
                    }
                  >
                    {stage.category}
                  </StepLabel>
                </Step>
              ))}
            </Stepper>

            {/* Pipeline definition with branches/loops */}
            {(() => {
              const pipelineDef: PipelineDefinition | undefined =
                pipelineData?.pipelineDefinitions?.find(
                  (p: PipelineDefinition) => p.name === task.pipeline
                )
              if (!pipelineDef) return null

              const hasLoops = pipelineDef.stages.some((s) => s.loop)
              const hasConditions = pipelineDef.stages.some((s) => s.conditions?.length > 0)
              if (!hasLoops && !hasConditions) return null

              return (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    Pipeline Definition (v{pipelineDef.version})
                  </Typography>
                  <Stack spacing={0.5}>
                    {pipelineDef.stages.map((stageDef, idx) => {
                      const isLoopTarget = pipelineDef.stages.some(
                        (s) => s.loop?.loopStages?.includes(stageDef.category)
                      )
                      return (
                        <Box key={idx}>
                          {/* Connector arrow */}
                          {idx > 0 && (
                            <Box sx={{ display: 'flex', alignItems: 'center', ml: 2, color: 'text.disabled' }}>
                              {stageDef.conditions?.length ? (
                                <Typography variant="caption" sx={{ fontStyle: 'italic', mr: 1 }}>
                                  if: {stageDef.conditions.join(' AND ')}
                                </Typography>
                              ) : null}
                              <ArrowForwardIcon sx={{ fontSize: 14, transform: 'rotate(90deg)' }} />
                            </Box>
                          )}

                          {/* Stage box */}
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 1,
                              p: 0.75,
                              pl: 1.5,
                              borderRadius: 1,
                              backgroundColor: isLoopTarget ? 'action.hover' : 'transparent',
                              border: isLoopTarget ? '1px dashed' : 'none',
                              borderColor: 'info.main',
                            }}
                          >
                            <Typography variant="body2" sx={{ minWidth: 24, color: 'text.secondary' }}>
                              {idx}
                            </Typography>
                            <Chip
                              label={stageDef.category}
                              size="small"
                              variant={stageDef.required ? 'filled' : 'outlined'}
                              color="default"
                              sx={{ fontWeight: 600 }}
                            />
                            {!stageDef.required && (
                              <Typography variant="caption" color="text.secondary">
                                optional
                              </Typography>
                            )}

                            {/* Loop indicator */}
                            {stageDef.loop && (
                              <Tooltip
                                title={
                                  <Box>
                                    <Typography variant="caption" display="block">
                                      <strong>Loop:</strong>{' '}
                                      {stageDef.loop.loopStages?.length
                                        ? stageDef.loop.loopStages.join(' → ')
                                        : stageDef.category}
                                    </Typography>
                                    <Typography variant="caption" display="block">
                                      <strong>Exit when:</strong> {stageDef.loop.condition}
                                    </Typography>
                                    <Typography variant="caption" display="block">
                                      <strong>Max repeats:</strong> {stageDef.loop.maxRepeats}x ({stageDef.loop.evalMode})
                                    </Typography>
                                  </Box>
                                }
                              >
                                <Chip
                                  icon={<RepeatIcon sx={{ fontSize: 14 }} />}
                                  label={`loop ${stageDef.loop.loopStages?.length ? stageDef.loop.loopStages.join(' → ') : stageDef.category} (max ${stageDef.loop.maxRepeats}x)`}
                                  size="small"
                                  variant="outlined"
                                  color="info"
                                  sx={{
                                    height: 22,
                                    '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' },
                                  }}
                                />
                              </Tooltip>
                            )}
                          </Box>
                        </Box>
                      )
                    })}
                  </Stack>
                </>
              )
            })()}
          </CardContent>
        </Card>
      )}

      {/* Stage output */}
      {stages.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Stage Output
          </Typography>
          {stages.map((stage) => {
              const output = stage.structuredOutput as Record<string, unknown> | null
              const findings = output?.findings as Array<{
                severity?: string
                file?: string
                line?: number
                message?: string
              }> | undefined
              const summary = output?.summary as string | undefined
              const recommendation = output?.recommendation as string | undefined

              return (
                <Accordion key={stage.id} variant="outlined" disableGutters>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <Typography fontWeight={600}>
                        Stage {stage.stageNumber}: {stage.category}
                      </Typography>
                      {stage.retryCount > 0 && (
                        <Tooltip title={`Iteration ${stage.retryCount + 1} (loop)`}>
                          <Chip
                            icon={<LoopIcon sx={{ fontSize: 14 }} />}
                            label={`iter ${stage.retryCount + 1}`}
                            size="small"
                            variant="outlined"
                            color="info"
                            sx={{ height: 20, '& .MuiChip-label': { px: 0.5, fontSize: '0.7rem' } }}
                          />
                        </Tooltip>
                      )}
                      {stage.agent && (
                        <Typography variant="caption" color="text.secondary">
                          {stage.agent}
                        </Typography>
                      )}
                      <StatusChip status={stage.status} size="small" />
                    </Stack>
                  </AccordionSummary>
                  <AccordionDetails>
                    {summary && (
                      <Typography variant="body2" sx={{ mb: 2 }}>
                        {summary}
                      </Typography>
                    )}
                    {recommendation && (
                      <Alert severity="info" sx={{ mb: 2 }}>
                        {recommendation}
                      </Alert>
                    )}
                    {findings && findings.length > 0 && (
                      <Stack spacing={1} sx={{ mb: 2 }}>
                        {findings.map((f, i) => (
                          <Box
                            key={i}
                            sx={{
                              p: 1.5,
                              borderRadius: 1,
                              backgroundColor: 'background.default',
                              border: '1px solid',
                              borderColor: 'divider',
                            }}
                          >
                            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                              {f.severity && (
                                <Chip
                                  label={f.severity}
                                  size="small"
                                  color={
                                    f.severity === 'error' || f.severity === 'critical'
                                      ? 'error'
                                      : f.severity === 'warning'
                                        ? 'warning'
                                        : 'default'
                                  }
                                />
                              )}
                              {f.file && (
                                <Typography variant="caption" sx={monoStyle}>
                                  {f.file}{f.line ? `:${f.line}` : ''}
                                </Typography>
                              )}
                            </Stack>
                            {f.message && (
                              <Typography variant="body2">{f.message}</Typography>
                            )}
                          </Box>
                        ))}
                      </Stack>
                    )}
                    {!findings && output && (
                      <Box
                        component="pre"
                        sx={{
                          m: 0,
                          p: 1.5,
                          backgroundColor: 'background.default',
                          borderRadius: 1,
                          overflow: 'auto',
                          ...monoStyle,
                          fontSize: '0.78rem',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {JSON.stringify(output, null, 2)}
                      </Box>
                    )}
                    {stage.status === 'EXECUTING' && stage.liveOutput && (
                      <Box
                        component="pre"
                        sx={{
                          m: 0,
                          mb: 2,
                          p: 1.5,
                          backgroundColor: '#1e1e1e',
                          color: '#d4d4d4',
                          borderRadius: 1,
                          overflow: 'auto',
                          maxHeight: 400,
                          ...monoStyle,
                          fontSize: '0.75rem',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {stage.liveOutput}
                      </Box>
                    )}
                    {!output && stage.rawOutput && (
                      <Box
                        component="pre"
                        sx={{
                          m: 0,
                          p: 1.5,
                          backgroundColor: 'background.default',
                          borderRadius: 1,
                          overflow: 'auto',
                          ...monoStyle,
                          fontSize: '0.78rem',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {stage.rawOutput}
                      </Box>
                    )}
                  </AccordionDetails>
                </Accordion>
              )
            })}
        </Box>
      )}

      {/* Context inspector */}
      {task.context?.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Context
          </Typography>
          {task.context.map((entry) => {
            const { display, isJson } = resolveContextValue(entry)
            return (
              <Accordion key={entry.id} variant="outlined" disableGutters>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Stack direction="row" spacing={2} alignItems="center">
                    <Typography sx={monoStyle} component="span">
                      {entry.key}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {isJson ? 'JSON' : entry.valueType}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {formatDate(entry.createdAt)}
                    </Typography>
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <Box
                    component="pre"
                    sx={{
                      m: 0,
                      p: 1.5,
                      backgroundColor: 'background.default',
                      borderRadius: 1,
                      overflow: 'auto',
                      ...monoStyle,
                      fontSize: '0.78rem',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {display}
                  </Box>
                </AccordionDetails>
              </Accordion>
            )
          })}
        </Box>
      )}

      {/* Action buttons */}
      <Stack direction="row" spacing={1}>
        {canRetry && (
          <Button
            variant="contained"
            color="warning"
            onClick={() => retryTask()}
            disabled={retrying}
            data-testid="btn-retry"
          >
            {retrying ? 'Retrying…' : 'Retry'}
          </Button>
        )}
        {canRerun && (
          <Button
            variant="contained"
            color="info"
            onClick={() => rerunTask()}
            disabled={rerunning}
            data-testid="btn-rerun"
          >
            {rerunning ? 'Creating…' : 'Rerun'}
          </Button>
        )}
        {canClose && (
          <Button
            variant="outlined"
            color="secondary"
            onClick={() => closeTask()}
            disabled={closing}
            data-testid="btn-close"
          >
            {closing ? 'Closing…' : 'Close'}
          </Button>
        )}
        {canCancel && (
          <Button
            variant="outlined"
            color="error"
            onClick={() => cancelTask()}
            disabled={cancelling}
            data-testid="btn-cancel"
          >
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </Button>
        )}
        {canUnblock && (
          <Button
            variant="contained"
            color="primary"
            onClick={() => setUnblockOpen(true)}
            data-testid="btn-unblock"
          >
            Unblock
          </Button>
        )}
      </Stack>

      {/* Unblock dialog */}
      <Dialog open={unblockOpen} onClose={() => setUnblockOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Unblock Task</DialogTitle>
        <DialogContent>
          <TextField
            label="Resolution"
            multiline
            rows={4}
            fullWidth
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            placeholder="Describe how this blockage was resolved..."
            sx={{ mt: 1 }}
            data-testid="unblock-resolution-input"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUnblockOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => unblockTask({ variables: { id, resolution } })}
            disabled={unblocking || !resolution.trim()}
            data-testid="btn-unblock-confirm"
          >
            {unblocking ? 'Unblocking…' : 'Unblock'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
