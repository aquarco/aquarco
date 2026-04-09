'use client'

import React from 'react'
import { useQuery } from '@apollo/client'
import { useParams } from 'next/navigation'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Grid from '@mui/material/Grid'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import Stack from '@mui/material/Stack'
import Divider from '@mui/material/Divider'
import { GET_TASK } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'
import { formatCost, formatTokens } from '@/lib/spending'
import type { Task } from './types'
import { PipelineStagesFlow } from '@/components/tasks/PipelineStagesFlow'
import { StageOutputSection } from '@/components/tasks/StageOutputSection'
import { ContextInspector } from '@/components/tasks/ContextInspector'
import { TaskActions } from '@/components/tasks/TaskActions'

export default function TaskDetailPage() {
  const params = useParams()
  const id = params?.id as string

  const { data, loading, error, refetch } = useQuery(GET_TASK, {
    variables: { id },
    skip: !id,
    pollInterval: 5000,
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
    return <Alert severity="error">Failed to load task: {error.message}</Alert>
  }

  const task: Task | undefined = data?.task
  if (!task) {
    return <Alert severity="warning">Task not found.</Alert>
  }

  // All stage runs sorted by execution_order ASC NULLS LAST.
  const stages = task.stages.slice().sort((a, b) => {
    const eoA = a.executionOrder
    const eoB = b.executionOrder
    if (eoA != null && eoB != null) return eoA - eoB
    if (eoA != null && eoB == null) return -1
    if (eoA == null && eoB != null) return 1
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })

  // Deduplicated stages for SVG diagram (latest run wins per stageNumber).
  const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
  const uniqueStagesMap = new Map<number, typeof stages[number]>()
  for (const s of stages) {
    if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
    uniqueStagesMap.set(s.stageNumber, s)
  }
  const uniqueStages = Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)

  // Derive current executing stage number from lastCompletedStageId.
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

  const effectiveExecutingStages = new Set<number>()
  if (task.status === 'EXECUTING' || task.status === 'PLANNING') {
    effectiveExecutingStages.add(currentStageNumber)
  }

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h5" fontWeight={700}>{task.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={monoStyle}>{task.id}</Typography>
        </Box>
        <StatusChip status={task.status} size="medium" />
      </Stack>

      {/* Task overview card */}
      <TaskOverviewCard task={task} stages={stages} currentStageNumber={currentStageNumber} effectiveExecutingStages={effectiveExecutingStages} />

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
      <StageOutputSection stages={stages} effectiveExecutingStages={effectiveExecutingStages} />

      {/* Context inspector */}
      <ContextInspector context={task.context} />

      {/* Action buttons */}
      <TaskActions taskId={task.id} status={task.status} onMutationComplete={() => refetch()} />
    </Box>
  )
}

/** Task metadata and spending overview card. */
function TaskOverviewCard({
  task, stages, currentStageNumber, effectiveExecutingStages,
}: {
  task: Task
  stages: Task['stages']
  currentStageNumber: number
  effectiveExecutingStages: Set<number>
}) {
  const totalCost = stages.reduce((sum, s) => sum + (s.costUsd ?? 0), 0)
  const totalInput = stages.reduce((sum, s) => sum + (s.tokensInput ?? 0), 0)
  const totalOutput = stages.reduce((sum, s) => sum + (s.tokensOutput ?? 0), 0)
  const totalCacheRead = stages.reduce((sum, s) => sum + (s.cacheReadTokens ?? 0), 0)
  const totalCacheWrite = stages.reduce((sum, s) => sum + (s.cacheWriteTokens ?? 0), 0)
  const hasExecuting = stages.some(s => s.status === 'EXECUTING' ||
    (s.status === 'PENDING' && effectiveExecutingStages.has(s.stageNumber)))
  const hasSpending = totalCost > 0 || totalInput > 0 || totalOutput > 0

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Grid container spacing={2}>
          {hasSpending && (
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
                  <Typography variant="caption" color="text.secondary">* includes live estimate from executing stages</Typography>
                </Grid>
              )}
              <Grid item xs={12}><Divider /></Grid>
            </>
          )}
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
  )
}
