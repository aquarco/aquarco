'use client'

import React, { useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import { useParams } from 'next/navigation'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
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
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import Divider from '@mui/material/Divider'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { GET_TASK, RETRY_TASK, RERUN_TASK, CLOSE_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'
import { formatCost, formatTokens } from '@/lib/spending'
import type { Task, ContextEntry } from './types'
import { StructuredOutputDisplay } from '@/components/tasks/StructuredOutputDisplay'
import { StageDuration } from '@/components/tasks/StageDuration'
import { PipelineStagesFlow } from '@/components/tasks/PipelineStagesFlow'
import { parseLiveOutput } from './utils'

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

  // All stage runs sorted by execution_order ASC NULLS LAST.
  // Falls back to (stageNumber, iteration, run) for stages without execution_order
  // (historical data or PENDING stages).
  const stages = task.stages.slice().sort((a, b) => {
    const eoA = a.executionOrder
    const eoB = b.executionOrder
    // Both have execution_order — sort by it
    if (eoA != null && eoB != null) return eoA - eoB
    // NULLS LAST: non-null before null
    if (eoA != null && eoB == null) return -1
    if (eoA == null && eoB != null) return 1
    // Both null — legacy fallback
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })

  // Deduplicated stages for the SVG diagram: one entry per unique stageNumber (last write wins = latest run).
  // Exclude system stages (planning, condition-eval) which are not part of the pipeline definition.
  const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
  const uniqueStagesMap = new Map<number, typeof stages[number]>()
  for (const s of stages) {
    if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
    uniqueStagesMap.set(s.stageNumber, s)
  }
  const uniqueStages = Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)

  // Derive the current executing stage number from lastCompletedStageId.
  // lastCompletedStageId is a stages.id FK — find the matching stage row to get its stageNumber,
  // then the next stageNumber is the one currently executing.
  const lastCompletedStage = task.lastCompletedStageId != null
    ? stages.find((s) => Number(s.id) === task.lastCompletedStageId)
    : null
  const currentStageNumber = lastCompletedStage != null
    ? (uniqueStages.find((s) => s.stageNumber > lastCompletedStage.stageNumber)?.stageNumber ?? lastCompletedStage.stageNumber + 1)
    : 0

  const activeStep = task.status === 'EXECUTING' || task.status === 'PLANNING'
    ? uniqueStages.findIndex((s) => s.stageNumber === currentStageNumber)
    : uniqueStages.findIndex(
        (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
      )

  // Build a set of stage numbers that should display as EXECUTING based on task state
  const effectiveExecutingStages = new Set<number>()
  if (task.status === 'EXECUTING' || task.status === 'PLANNING') {
    effectiveExecutingStages.add(currentStageNumber)
  }

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

      {/* Task overview card */}
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Grid container spacing={2}>
            {/* Spending row */}
            {(() => {
              const totalCost = stages.reduce((sum, s) => sum + (s.costUsd ?? 0), 0)
              const totalInput = stages.reduce((sum, s) => sum + (s.tokensInput ?? 0), 0)
              const totalOutput = stages.reduce((sum, s) => sum + (s.tokensOutput ?? 0), 0)
              const totalCacheRead = stages.reduce((sum, s) => sum + (s.cacheReadTokens ?? 0), 0)
              const totalCacheWrite = stages.reduce((sum, s) => sum + (s.cacheWriteTokens ?? 0), 0)
              const hasExecuting = stages.some(s => s.status === 'EXECUTING' ||
                (s.status === 'PENDING' && effectiveExecutingStages.has(s.stageNumber)))
              const hasAny = totalCost > 0 || totalInput > 0 || totalOutput > 0

              if (!hasAny) return null
              return (
                <>
                  <Grid item xs={6} sm={4} md={2}>
                    <Typography variant="caption" color="text.secondary">Cost</Typography>
                    <Typography variant="h6" fontWeight={700} color="warning.main">
                      {formatCost(totalCost)}{hasExecuting ? '*' : ''}
                    </Typography>
                  </Grid>
                  <Grid item xs={6} sm={4} md={2}>
                    <Typography variant="caption" color="text.secondary">Input Tokens</Typography>
                    <Typography variant="body1" fontWeight={600}>{formatTokens(totalInput)}</Typography>
                  </Grid>
                  <Grid item xs={6} sm={4} md={2}>
                    <Typography variant="caption" color="text.secondary">Output Tokens</Typography>
                    <Typography variant="body1" fontWeight={600}>{formatTokens(totalOutput)}</Typography>
                  </Grid>
                  {totalCacheRead > 0 && (
                    <Grid item xs={6} sm={4} md={2}>
                      <Typography variant="caption" color="text.secondary">Cache Read</Typography>
                      <Typography variant="body1" fontWeight={600}>{formatTokens(totalCacheRead)}</Typography>
                    </Grid>
                  )}
                  {totalCacheWrite > 0 && (
                    <Grid item xs={6} sm={4} md={2}>
                      <Typography variant="caption" color="text.secondary">Cache Write</Typography>
                      <Typography variant="body1" fontWeight={600}>{formatTokens(totalCacheWrite)}</Typography>
                    </Grid>
                  )}
                  {hasExecuting && (
                    <Grid item xs={12}>
                      <Typography variant="caption" color="text.secondary">
                        * includes live estimate from executing stages
                      </Typography>
                    </Grid>
                  )}
                  <Grid item xs={12}>
                    <Divider />
                  </Grid>
                </>
              )
            })()}
            {/* Metadata — row 1: identity */}
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Source</Typography>
              <Typography variant="body2">{task.source}</Typography>
            </Grid>
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Repository</Typography>
              <Typography variant="body2">{task.repository.name}</Typography>
            </Grid>
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Pipeline</Typography>
              <Typography variant="body2">{task.pipeline}</Typography>
            </Grid>
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Priority</Typography>
              <Typography variant="body2">{task.priority}</Typography>
            </Grid>
            {/* Metadata — row 2: progress */}
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Current Stage</Typography>
              <Typography variant="body2">{currentStageNumber}</Typography>
            </Grid>
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Retry Count</Typography>
              <Typography variant="body2">{task.retryCount}</Typography>
            </Grid>
            <Grid item xs={6} sm={4} md={3}>
              <Typography variant="caption" color="text.secondary">Started</Typography>
              <Typography variant="body2">{formatDate(task.startedAt)}</Typography>
            </Grid>
            {/* Metadata — row 3: completion */}
            <Grid item xs={6} sm={4} md={3}>
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

      {/* Pipeline stages flow diagram */}
      {uniqueStages.length > 0 && (
        <PipelineStagesFlow
          stages={uniqueStages}
          activeStep={activeStep}
          pipelineName={task.pipeline}
          effectiveExecutingStages={effectiveExecutingStages}
        />
      )}

      {/* Stage output */}
      {stages.length > 0 && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" fontWeight={700} gutterBottom>
              Stage Output
            </Typography>
            <Stack spacing={1}>
              {(() => {
                const runCountPerStageNumber = new Map<number, number>()
                const items: React.ReactNode[] = []

                for (const stage of stages) {
                  const stageNum = stage.stageNumber
                  const runCount = (runCountPerStageNumber.get(stageNum) ?? 0) + 1
                  runCountPerStageNumber.set(stageNum, runCount)

                  let runSuffix = ''
                  if (runCount === 2) runSuffix = ' (next run)'
                  else if (runCount === 3) runSuffix = ' (3rd run)'
                  else if (runCount > 3) runSuffix = ` (${runCount}th run)`

                  const effectiveStatus = stage.status === 'PENDING' && effectiveExecutingStages.has(stage.stageNumber)
                    ? 'EXECUTING' : stage.status
                  const isLive = effectiveStatus === 'EXECUTING'
                  const stageCost = stage.costUsd
                  const stageTotalTokens = (stage.tokensInput ?? 0) + (stage.tokensOutput ?? 0) + (stage.cacheReadTokens ?? 0) + (stage.cacheWriteTokens ?? 0)

                  const output = stage.structuredOutput as Record<string, unknown> | null
                  const conditionMessage = output?._condition_message as string | undefined

                  items.push(
                    <Accordion key={stage.id} variant="outlined" disableGutters sx={{ '&:before': { display: 'none' } }}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2, minHeight: 48 }}>
                        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ width: '100%', mr: 1 }}>
                          <Stack direction="row" spacing={1.5} alignItems="center">
                            <StatusChip status={effectiveStatus} size="small" />
                            <Typography variant="body2" fontWeight={600}>
                              {stage.category}{runSuffix}
                            </Typography>
                            {stage.agent && (
                              <Typography variant="caption" color="text.secondary">
                                {stage.agent}
                              </Typography>
                            )}
                          </Stack>
                          <Stack direction="row" spacing={1.5} alignItems="center">
                            {stageCost != null && stageCost > 0 && (
                              <Typography variant="caption" fontWeight={600} color="warning.main">
                                {isLive ? '~' : ''}{formatCost(stageCost)}
                              </Typography>
                            )}
                            {stageTotalTokens > 0 && (
                              <Typography variant="caption" color="text.secondary" sx={monoStyle}>
                                {formatTokens(stageTotalTokens)}
                              </Typography>
                            )}
                            <StageDuration
                              startedAt={stage.startedAt}
                              completedAt={stage.completedAt}
                              isExecuting={isLive}
                            />
                          </Stack>
                        </Stack>
                      </AccordionSummary>
                      <AccordionDetails sx={{ px: 2, pt: 0, pb: 2 }}>
                        {/* Token stats bar */}
                        {(() => {
                          const inp = stage.tokensInput
                          const out = stage.tokensOutput
                          const cr = stage.cacheReadTokens
                          const cw = stage.cacheWriteTokens
                          if (!inp && !out) return null
                          return (
                            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap justifyContent="flex-end"
                              sx={{ mb: 2, py: 1, px: 1.5, borderRadius: 1, backgroundColor: 'action.hover' }}
                            >
                              {inp != null && inp > 0 && (
                                <Typography variant="caption" color="text.secondary">
                                  Input: {formatTokens(inp)}
                                </Typography>
                              )}
                              {out != null && out > 0 && (
                                <Typography variant="caption" color="text.secondary">
                                  Output: {formatTokens(out)}
                                </Typography>
                              )}
                              {cr != null && cr > 0 && (
                                <Typography variant="caption" color="text.secondary">
                                  Cache Read: {formatTokens(cr)}
                                </Typography>
                              )}
                              {cw != null && cw > 0 && (
                                <Typography variant="caption" color="text.secondary">
                                  Cache Write: {formatTokens(cw)}
                                </Typography>
                              )}
                            </Stack>
                          )
                        })()}
                        {/* Structured output */}
                        {output && <StructuredOutputDisplay output={output} />}
                        {/* Live output stream (parsed) */}
                        {stage.liveOutput && (() => {
                          const lines = parseLiveOutput(stage.liveOutput)
                          if (lines.length === 0) return null
                          return (
                            <Box
                              sx={{
                                mt: 1,
                                p: 1.5,
                                backgroundColor: '#1e1e1e',
                                color: '#d4d4d4',
                                borderRadius: 1,
                                overflow: 'auto',
                                maxHeight: 400,
                                ...monoStyle,
                                fontSize: '0.75rem',
                              }}
                            >
                              {lines.map((line, i) => (
                                <Typography
                                  key={i}
                                  variant="body2"
                                  component="div"
                                  sx={{
                                    color: '#d4d4d4',
                                    ...monoStyle,
                                    fontSize: '0.75rem',
                                    whiteSpace: 'pre-wrap',
                                    wordBreak: 'break-word',
                                    mb: 0.5,
                                  }}
                                >
                                  {line}
                                </Typography>
                              ))}
                            </Box>
                          )
                        })()}
                      </AccordionDetails>
                    </Accordion>
                  )

                  // Evaluation block between stages
                  if (conditionMessage && stage.status === 'COMPLETED') {
                    items.push(
                      <Box
                        key={`eval-${stage.id}`}
                        sx={{
                          px: 2,
                          py: 1,
                          borderLeft: '4px solid',
                          borderColor: 'info.main',
                          backgroundColor: 'action.hover',
                          borderRadius: '0 4px 4px 0',
                        }}
                      >
                        <Stack direction="row" spacing={1} alignItems="baseline">
                          <Typography variant="caption" fontWeight={700} color="info.main" sx={{ textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Evaluation
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            {conditionMessage}
                          </Typography>
                        </Stack>
                      </Box>
                    )
                  }
                }

                return items
              })()}
            </Stack>
          </CardContent>
        </Card>
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
