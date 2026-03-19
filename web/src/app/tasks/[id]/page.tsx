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
import { GET_TASK, RETRY_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'
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
  const canRetry = status === 'FAILED' || status === 'TIMEOUT'
  const canCancel = status === 'PENDING' || status === 'QUEUED' || status === 'EXECUTING'
  const canUnblock = status === 'BLOCKED'

  const stages = task.stages.slice().sort((a, b) => a.stageNumber - b.stageNumber)
  const activeStep = stages.findIndex(
    (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
  )

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
              {stages.map((stage) => (
                <Step
                  key={stage.id}
                  completed={stage.status === 'COMPLETED'}
                >
                  <StepLabel
                    error={stage.status === 'FAILED'}
                    optional={
                      stage.agent ? (
                        <Typography variant="caption" color="text.secondary" display="block" textAlign="center">
                          {stage.agent}
                        </Typography>
                      ) : undefined
                    }
                  >
                    {stage.category}
                  </StepLabel>
                </Step>
              ))}
            </Stepper>
          </CardContent>
        </Card>
      )}

      {/* Stage output */}
      {stages.some((s) => s.structuredOutput || s.rawOutput) && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Stage Output
          </Typography>
          {stages
            .filter((s) => s.structuredOutput || s.rawOutput)
            .map((stage) => {
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
