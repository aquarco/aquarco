'use client'

import React, { useState, useEffect, useCallback } from 'react'
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
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import Tooltip from '@mui/material/Tooltip'
import Divider from '@mui/material/Divider'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { useTheme } from '@mui/material/styles'
import { GET_TASK, GET_PIPELINE_DEFINITIONS, RETRY_TASK, RERUN_TASK, CLOSE_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'
import { formatCost, formatTokens } from '@/lib/spending'

interface Stage {
  id: string
  stageNumber: number
  iteration: number
  run: number
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
  costUsd: number | null
  cacheReadTokens: number | null
  cacheWriteTokens: number | null
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
  lastCompletedStageId: number | null
  checkpointData: Record<string, unknown> | null
  pipelineVersion: string | null
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

function formatDurationSeconds(totalSeconds: number): string {
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  if (minutes < 60) return `${minutes}m ${secs}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function StageDuration({ startedAt, completedAt, isExecuting }: {
  startedAt: string | null
  completedAt: string | null
  isExecuting: boolean
}) {
  const computeSeconds = useCallback(() => {
    if (!startedAt) return 0
    const end = completedAt ? new Date(completedAt).getTime() : Date.now()
    return Math.max(0, Math.floor((end - new Date(startedAt).getTime()) / 1000))
  }, [startedAt, completedAt])

  const [seconds, setSeconds] = useState(computeSeconds)

  useEffect(() => {
    setSeconds(computeSeconds())
    if (!isExecuting) return
    const id = setInterval(() => setSeconds(computeSeconds()), 1000)
    return () => clearInterval(id)
  }, [isExecuting, computeSeconds])

  if (!startedAt) return null
  return (
    <Typography variant="caption" color="text.secondary">
      {formatDurationSeconds(seconds)}
    </Typography>
  )
}

// ── Pipeline Stages Flow Diagram ─────────────────────────────────────────────

interface PipelineCondition {
  type: string
  expression: string
  onYes: string | null
  onNo: string | null
  maxRepeats: number | null
}

interface PipelineStageDefn {
  name: string
  category: string
  required: boolean
  conditions: PipelineCondition[]
}

const FLOW_NODE_W = 140
const FLOW_NODE_H = 52
const FLOW_GAP_X = 40
const FLOW_PAD_X = 30
const FLOW_PAD_TOP = 50 // extra room for self-loops above
const FLOW_PAD_BOT = 20

const STATUS_COLORS: Record<string, string> = {
  COMPLETED: '#2e7d32',
  EXECUTING: '#ed6c02',
  FAILED: '#d32f2f',
  SKIPPED: '#9e9e9e',
  PENDING: '#bdbdbd',
  RATE_LIMITED: '#f57c00',
}

function flowNodeX(idx: number): number {
  return FLOW_PAD_X + idx * (FLOW_NODE_W + FLOW_GAP_X)
}

function truncateExpr(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

function PipelineStagesFlow({
  stages,
  activeStep,
  pipelineName,
  effectiveExecutingStages,
}: {
  stages: Stage[]  // deduplicated: one entry per unique stageNumber (latest run wins)
  activeStep: number
  pipelineName: string
  effectiveExecutingStages: Set<number>
}) {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  // Fetch pipeline definition to get conditions
  const { data: pipeData } = useQuery(GET_PIPELINE_DEFINITIONS)
  const pipelineDefs = (pipeData?.pipelineDefinitions ?? []) as Array<{
    name: string
    stages: PipelineStageDefn[]
  }>
  const defn = pipelineDefs.find((p) => p.name === pipelineName)
  const defnStages = defn?.stages ?? []

  // Build name→index map from the pipeline definition
  const nameToIdx = new Map<string, number>()
  defnStages.forEach((s, i) => nameToIdx.set(s.name, i))

  // Build conditional edges
  interface FlowEdge {
    from: number; to: number; label: string; condType: string; isSelf: boolean
  }
  const edges: FlowEdge[] = []
  const seenEdges = new Set<string>()
  defnStages.forEach((s, i) => {
    for (const c of s.conditions) {
      for (const [outcome, target] of [['yes', c.onYes], ['no', c.onNo]] as const) {
        if (!target) continue
        const targetIdx = nameToIdx.get(target)
        if (targetIdx === undefined) continue
        const key = `${i}->${targetIdx}:${outcome}`
        if (seenEdges.has(key)) continue
        seenEdges.add(key)
        const maxR = c.maxRepeats ? ` (×${c.maxRepeats})` : ''
        const lbl = c.type === 'ai'
          ? `AI: ${outcome}${maxR}`
          : `${outcome}: ${truncateExpr(c.expression, 24)}${maxR}`
        edges.push({ from: i, to: targetIdx, label: lbl, condType: c.type, isSelf: i === targetIdx })
      }
    }
  })

  // Use the longer of runtime stages vs definition stages
  const count = Math.max(stages.length, defnStages.length)
  const svgW = FLOW_PAD_X * 2 + count * (FLOW_NODE_W + FLOW_GAP_X) - FLOW_GAP_X
  const svgH = FLOW_PAD_TOP + FLOW_NODE_H + FLOW_PAD_BOT + (edges.some((e) => !e.isSelf && e.to < e.from) ? 60 : 0)
  const nodeY = FLOW_PAD_TOP

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Pipeline Stages
        </Typography>
        <Box sx={{ overflowX: 'auto', width: 0, minWidth: '100%' }}>
          <svg width={svgW} height={svgH} viewBox={`0 0 ${svgW} ${svgH}`} style={{ display: 'block' }}>
            <defs>
              <marker id="pf-arrow-fwd" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto">
                <polygon points="0 0, 7 2.5, 0 5" fill={isDark ? '#555' : '#bbb'} />
              </marker>
            </defs>

            {/* Forward arrows (linear flow) */}
            {Array.from({ length: count - 1 }, (_, i) => {
              const x1 = flowNodeX(i) + FLOW_NODE_W
              const x2 = flowNodeX(i + 1)
              const cy = nodeY + FLOW_NODE_H / 2
              return (
                <line key={`fwd-${i}`} x1={x1} y1={cy} x2={x2} y2={cy}
                  stroke={isDark ? '#555' : '#bbb'} strokeWidth={1.5} markerEnd="url(#pf-arrow-fwd)" />
              )
            })}

            {/* Conditional edges */}
            {edges.map((edge, ei) => {
              const color = edge.condType === 'ai' ? '#7c4dff' : (isDark ? '#90caf9' : '#1976d2')
              const dash = edge.condType === 'ai' ? '5 3' : undefined
              const markerId = `pf-arr-${ei}`

              if (edge.isSelf) {
                // Self-loop arc above the node
                const cx = flowNodeX(edge.from) + FLOW_NODE_W / 2
                const top = nodeY - 2
                return (
                  <g key={`edge-${ei}`}>
                    <defs>
                      <marker id={markerId} markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto">
                        <polygon points="0 0, 7 2.5, 0 5" fill={color} />
                      </marker>
                    </defs>
                    <path
                      d={`M ${cx - 16} ${top} C ${cx - 36} ${top - 38}, ${cx + 36} ${top - 38}, ${cx + 16} ${top}`}
                      fill="none" stroke={color} strokeWidth={1.5}
                      strokeDasharray={dash} markerEnd={`url(#${markerId})`}
                    />
                    <text x={cx} y={top - 28} textAnchor="middle" fontSize={8} fill={color} fontFamily="Inter, sans-serif">
                      {edge.label}
                    </text>
                  </g>
                )
              }

              // Backward edge (goes left): arc below
              if (edge.to < edge.from) {
                const x1 = flowNodeX(edge.from) + FLOW_NODE_W / 2
                const x2 = flowNodeX(edge.to) + FLOW_NODE_W / 2
                const bot = nodeY + FLOW_NODE_H + 4
                const arcY = bot + 30
                return (
                  <g key={`edge-${ei}`}>
                    <defs>
                      <marker id={markerId} markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto">
                        <polygon points="0 0, 7 2.5, 0 5" fill={color} />
                      </marker>
                    </defs>
                    <path
                      d={`M ${x1} ${bot} C ${x1} ${arcY}, ${x2} ${arcY}, ${x2} ${bot}`}
                      fill="none" stroke={color} strokeWidth={1.5}
                      strokeDasharray={dash} markerEnd={`url(#${markerId})`}
                    />
                    <text x={(x1 + x2) / 2} y={arcY + 12} textAnchor="middle" fontSize={8} fill={color} fontFamily="Inter, sans-serif">
                      {edge.label}
                    </text>
                  </g>
                )
              }

              // Forward jump (skip stages): arc above
              const x1 = flowNodeX(edge.from) + FLOW_NODE_W / 2
              const x2 = flowNodeX(edge.to) + FLOW_NODE_W / 2
              const top = nodeY - 2
              const arcY = top - 30
              return (
                <g key={`edge-${ei}`}>
                  <defs>
                    <marker id={markerId} markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto">
                      <polygon points="0 0, 7 2.5, 0 5" fill={color} />
                    </marker>
                  </defs>
                  <path
                    d={`M ${x1} ${top} C ${x1} ${arcY}, ${x2} ${arcY}, ${x2} ${top}`}
                    fill="none" stroke={color} strokeWidth={1.5}
                    strokeDasharray={dash} markerEnd={`url(#${markerId})`}
                  />
                  <text x={(x1 + x2) / 2} y={arcY - 4} textAnchor="middle" fontSize={8} fill={color} fontFamily="Inter, sans-serif">
                    {edge.label}
                  </text>
                </g>
              )
            })}

            {/* Stage nodes */}
            {Array.from({ length: count }, (_, i) => {
              const x = flowNodeX(i)
              const runtimeStage = stages[i]
              const defStage = defnStages[i]
              const rawStatus = runtimeStage?.status ?? 'PENDING'
              const status = rawStatus === 'PENDING' && runtimeStage && effectiveExecutingStages.has(runtimeStage.stageNumber)
                ? 'EXECUTING' : rawStatus
              const borderColor = STATUS_COLORS[status] ?? '#bdbdbd'
              const isActive = i === activeStep
              const name = defStage?.name ?? runtimeStage?.category ?? `Stage ${i + 1}`
              const agent = runtimeStage?.agent ?? null

              return (
                <g key={`node-${i}`}>
                  <rect
                    x={x} y={nodeY} width={FLOW_NODE_W} height={FLOW_NODE_H}
                    rx={8} ry={8}
                    fill={isDark ? '#1e293b' : '#fff'}
                    stroke={borderColor} strokeWidth={isActive ? 3 : 2}
                  />
                  {/* Completed checkmark or step number */}
                  {status === 'COMPLETED' ? (
                    <>
                      <circle cx={x + FLOW_NODE_W / 2} cy={nodeY - 10} r={10} fill={STATUS_COLORS.COMPLETED} />
                      <text x={x + FLOW_NODE_W / 2} y={nodeY - 6} textAnchor="middle" fontSize={12} fill="#fff" fontFamily="sans-serif">✓</text>
                    </>
                  ) : (
                    <>
                      <circle cx={x + FLOW_NODE_W / 2} cy={nodeY - 10} r={10}
                        fill={isActive ? '#1976d2' : (isDark ? '#444' : '#e0e0e0')} />
                      <text x={x + FLOW_NODE_W / 2} y={nodeY - 6} textAnchor="middle"
                        fontSize={10} fontWeight={700}
                        fill={isActive ? '#fff' : (isDark ? '#aaa' : '#666')}
                        fontFamily="Inter, sans-serif">
                        {i + 1}
                      </text>
                    </>
                  )}
                  {/* Stage name */}
                  <text x={x + FLOW_NODE_W / 2} y={nodeY + 22} textAnchor="middle"
                    fontSize={11} fontWeight={700}
                    fill={isDark ? '#e0e0e0' : '#333'}
                    fontFamily="Inter, sans-serif">
                    {name.length > 18 ? name.slice(0, 17) + '…' : name}
                  </text>
                  {/* Agent name */}
                  <text x={x + FLOW_NODE_W / 2} y={nodeY + 38} textAnchor="middle"
                    fontSize={9}
                    fill={isDark ? '#888' : '#999'}
                    fontFamily="Inter, sans-serif">
                    {agent ?? (runtimeStage?.category ?? '')}
                  </text>
                </g>
              )
            })}
          </svg>
        </Box>
      </CardContent>
    </Card>
  )
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

  // All stage runs sorted chronologically for the history list
  const stages = task.stages.slice().sort((a, b) => {
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })

  // Deduplicated stages for the SVG diagram: one entry per unique stageNumber (last write wins = latest run).
  // Exclude system stages (planning, condition-eval) which are not part of the pipeline definition.
  const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
  const uniqueStagesMap = new Map<number, Stage>()
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
                  const findings = output?.findings as Array<{
                    severity?: string
                    file?: string
                    line?: number
                    message?: string
                  }> | undefined
                  const summary = output?.summary as string | undefined
                  const recommendation = output?.recommendation as string | undefined
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
                        {/* Summary & recommendation */}
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
                        {/* Findings */}
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
                        {/* Structured output (when no findings) */}
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
                        {/* Live output stream */}
                        {effectiveStatus === 'EXECUTING' && stage.liveOutput && (
                          <Box
                            component="pre"
                            sx={{
                              m: 0,
                              mt: 1,
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
                        {/* Raw output fallback */}
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
