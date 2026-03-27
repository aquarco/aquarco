'use client'

import React, { useState } from 'react'
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
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { useTheme } from '@mui/material/styles'
import { GET_TASK, GET_PIPELINE_DEFINITIONS, RETRY_TASK, RERUN_TASK, CLOSE_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'

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
}: {
  stages: Stage[]  // deduplicated: one entry per unique stageNumber (latest run wins)
  activeStep: number
  pipelineName: string
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
              const status = runtimeStage?.status ?? 'PENDING'
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

  // Deduplicated stages for the SVG diagram: one entry per unique stageNumber (last write wins = latest run)
  const uniqueStagesMap = new Map<number, Stage>()
  for (const s of stages) {
    uniqueStagesMap.set(s.stageNumber, s)
  }
  const uniqueStages = Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)

  const activeStep = uniqueStages.findIndex(
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

      {/* Pipeline stages flow diagram */}
      {uniqueStages.length > 0 && (
        <PipelineStagesFlow
          stages={uniqueStages}
          activeStep={activeStep}
          pipelineName={task.pipeline}
        />
      )}

      {/* Stage output */}
      {stages.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Stage Output
          </Typography>
          {(() => {
            // Build flat chronological history: stage accordions interleaved with evaluation blocks
            const runCountPerStageNumber = new Map<number, number>()
            const items: React.ReactNode[] = []

            for (const stage of stages) {
              const stageNum = stage.stageNumber
              const runCount = (runCountPerStageNumber.get(stageNum) ?? 0) + 1
              runCountPerStageNumber.set(stageNum, runCount)

              // Run label suffix: first run has no suffix, second is "(next run)", third is "(3rd run)", etc.
              let runSuffix = ''
              if (runCount === 2) runSuffix = ' (next run)'
              else if (runCount === 3) runSuffix = ' (3rd run)'
              else if (runCount > 3) runSuffix = ` (${runCount}th run)`

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
                <Accordion key={stage.id} variant="outlined" disableGutters>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <Typography fontWeight={600}>
                        Stage {stage.stageNumber + 1}: {stage.category}{runSuffix}
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

              // After each completed stage, emit an evaluation block if a condition message is present
              if (conditionMessage && stage.status === 'COMPLETED') {
                items.push(
                  <Box
                    key={`eval-${stage.id}`}
                    sx={{
                      px: 2,
                      py: 1,
                      my: 0.5,
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
