'use client'

import React from 'react'
import { useQuery } from '@apollo/client'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Paper from '@mui/material/Paper'
import Chip from '@mui/material/Chip'
import Alert from '@mui/material/Alert'
import Skeleton from '@mui/material/Skeleton'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import { useTheme } from '@mui/material/styles'
import { GET_PIPELINE_DEFINITIONS } from '@/lib/graphql/queries'

// ── Types ────────────────────────────────────────────────────────────────────

interface PipelineCondition {
  type: string
  expression: string
  onYes: string | null
  onNo: string | null
  maxRepeats: number | null
}

interface PipelineStage {
  name: string
  category: string
  required: boolean
  conditions: PipelineCondition[]
}

interface PipelineDefinition {
  name: string
  version: string
  stages: PipelineStage[]
  categories: Record<string, unknown>
}

// ── Constants ────────────────────────────────────────────────────────────────

const NODE_W = 140
const NODE_H = 52
const GAP_X = 40
const PAD_X = 30
const PAD_TOP = 50
const PAD_BOT = 20

const CATEGORY_COLORS: Record<string, string> = {
  analyze: '#1976d2',
  design: '#7c4dff',
  implementation: '#2e7d32',
  review: '#ed6c02',
  test: '#0288d1',
  docs: '#6d4c41',
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function nodeX(idx: number): number {
  return PAD_X + idx * (NODE_W + GAP_X)
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

interface Edge {
  from: number
  to: number
  label: string
  condType: string
  isSelf: boolean
}

function buildEdges(stages: PipelineStage[]): Edge[] {
  const nameToIdx = new Map<string, number>()
  stages.forEach((s, i) => nameToIdx.set(s.name, i))

  const edges: Edge[] = []
  const seen = new Set<string>()

  for (let i = 0; i < stages.length; i++) {
    const stage = stages[i]
    for (const cond of stage.conditions) {
      for (const [outcome, target] of [
        ['yes', cond.onYes],
        ['no', cond.onNo],
      ] as const) {
        if (!target) continue
        const targetIdx = nameToIdx.get(target)
        if (targetIdx === undefined) continue
        const key = `${i}->${targetIdx}:${outcome}`
        if (seen.has(key)) continue
        seen.add(key)

        const maxR = cond.maxRepeats ? ` (×${cond.maxRepeats})` : ''
        const label =
          cond.type === 'ai'
            ? `AI: ${outcome}${maxR}`
            : `${outcome}: ${truncate(cond.expression, 24)}${maxR}`

        edges.push({
          from: i,
          to: targetIdx,
          label,
          condType: cond.type,
          isSelf: i === targetIdx,
        })
      }
    }
  }

  return edges
}

// ── Pipeline flow diagram (single-line horizontal) ───────────────────────────

function PipelineFlow({ pipeline, uid }: { pipeline: PipelineDefinition; uid: string }) {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'
  const { stages } = pipeline
  const edges = buildEdges(stages)

  const count = stages.length
  const svgW = PAD_X * 2 + count * (NODE_W + GAP_X) - GAP_X
  const hasBackward = edges.some((e) => !e.isSelf && e.to < e.from)
  const svgH = PAD_TOP + NODE_H + PAD_BOT + (hasBackward ? 60 : 0)
  const nodeY = PAD_TOP

  return (
    <Box sx={{ overflowX: 'auto', width: 0, minWidth: '100%' }}>
      <svg width={svgW} height={svgH} viewBox={`0 0 ${svgW} ${svgH}`} style={{ display: 'block' }}>
        <defs>
          <marker id={`${uid}-fwd`} markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto">
            <polygon points="0 0, 7 2.5, 0 5" fill={isDark ? '#555' : '#bbb'} />
          </marker>
        </defs>

        {/* Forward arrows (linear flow) */}
        {Array.from({ length: count - 1 }, (_, i) => {
          const x1 = nodeX(i) + NODE_W
          const x2 = nodeX(i + 1)
          const cy = nodeY + NODE_H / 2
          return (
            <line key={`fwd-${i}`} x1={x1} y1={cy} x2={x2} y2={cy}
              stroke={isDark ? '#555' : '#bbb'} strokeWidth={1.5} markerEnd={`url(#${uid}-fwd)`} />
          )
        })}

        {/* Conditional edges */}
        {edges.map((edge, ei) => {
          const color = edge.condType === 'ai' ? '#7c4dff' : (isDark ? '#90caf9' : '#1976d2')
          const dash = edge.condType === 'ai' ? '5 3' : undefined
          const markerId = `${uid}-e${ei}`

          if (edge.isSelf) {
            const cx = nodeX(edge.from) + NODE_W / 2
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

          // Backward edge: arc below
          if (edge.to < edge.from) {
            const x1 = nodeX(edge.from) + NODE_W / 2
            const x2 = nodeX(edge.to) + NODE_W / 2
            const bot = nodeY + NODE_H + 4
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

          // Forward jump: arc above
          const x1 = nodeX(edge.from) + NODE_W / 2
          const x2 = nodeX(edge.to) + NODE_W / 2
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
        {stages.map((stage, i) => {
          const x = nodeX(i)
          const color = CATEGORY_COLORS[stage.category] ?? '#616161'
          return (
            <g key={stage.name}>
              {/* Step number badge above */}
              <circle cx={x + NODE_W / 2} cy={nodeY - 10} r={10} fill={color} />
              <text x={x + NODE_W / 2} y={nodeY - 6} textAnchor="middle"
                fontSize={10} fontWeight={700} fill="#fff" fontFamily="Inter, sans-serif">
                {i + 1}
              </text>
              {/* Node box */}
              <rect
                x={x} y={nodeY} width={NODE_W} height={NODE_H}
                rx={8} ry={8}
                fill={isDark ? '#1e293b' : '#fff'}
                stroke={color} strokeWidth={2}
              />
              <text x={x + NODE_W / 2} y={nodeY + 22} textAnchor="middle"
                fontSize={11} fontWeight={700} fill={color} fontFamily="Inter, sans-serif">
                {stage.name.length > 18 ? stage.name.slice(0, 17) + '…' : stage.name}
              </text>
              <text x={x + NODE_W / 2} y={nodeY + 38} textAnchor="middle"
                fontSize={9} fill={isDark ? '#aaa' : '#888'} fontFamily="Inter, sans-serif">
                {stage.category}{!stage.required ? ' (opt)' : ''}
              </text>
            </g>
          )
        })}
      </svg>
    </Box>
  )
}

// ── Condition details table ──────────────────────────────────────────────────

function ConditionsSummary({ stages }: { stages: PipelineStage[] }) {
  const conditionRows: Array<{
    stage: string
    stageIdx: number
    type: string
    expression: string
    onYes: string | null
    onNo: string | null
    maxRepeats: number | null
  }> = []

  stages.forEach((s, i) => {
    for (const c of s.conditions) {
      conditionRows.push({
        stage: s.name,
        stageIdx: i + 1,
        type: c.type,
        expression: c.expression,
        onYes: c.onYes,
        onNo: c.onNo,
        maxRepeats: c.maxRepeats,
      })
    }
  })

  if (conditionRows.length === 0) return null

  return (
    <Box sx={{ mt: 2, overflowX: 'auto' }}>
      <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
        Conditions
      </Typography>
      <Box
        component="table"
        sx={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: '0.8rem',
          '& th, & td': {
            px: 1.5,
            py: 0.75,
            borderBottom: '1px solid',
            borderColor: 'divider',
            textAlign: 'left',
          },
          '& th': { fontWeight: 700, color: 'text.secondary' },
        }}
      >
        <thead>
          <tr>
            <th>Stage</th>
            <th>Type</th>
            <th>Expression</th>
            <th>Yes →</th>
            <th>No →</th>
            <th>Max</th>
          </tr>
        </thead>
        <tbody>
          {conditionRows.map((r, i) => (
            <tr key={i}>
              <td>
                <Chip label={`${r.stageIdx}. ${r.stage}`} size="small" variant="outlined" />
              </td>
              <td>
                <Chip
                  label={r.type}
                  size="small"
                  color={r.type === 'ai' ? 'secondary' : 'primary'}
                  variant="filled"
                  sx={{ fontSize: '0.7rem' }}
                />
              </td>
              <td style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{r.expression}</td>
              <td>{r.onYes ? <Chip label={r.onYes} size="small" variant="outlined" color="success" /> : '—'}</td>
              <td>{r.onNo ? <Chip label={r.onNo} size="small" variant="outlined" color="warning" /> : '—'}</td>
              <td>{r.maxRepeats ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </Box>
    </Box>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function PipelinesPage() {
  const { data, loading, error } = useQuery(GET_PIPELINE_DEFINITIONS)
  const pipelines: PipelineDefinition[] = data?.pipelineDefinitions ?? []

  return (
    <Box>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Pipelines
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load pipeline definitions: {error.message}
        </Alert>
      )}

      {loading && (
        <Stack spacing={2}>
          {[...Array(2)].map((_, i) => (
            <Skeleton key={i} variant="rectangular" height={200} sx={{ borderRadius: 1 }} />
          ))}
        </Stack>
      )}

      {!loading && pipelines.length === 0 && !error && (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center', color: 'text.secondary' }}>
          <Typography>No pipeline definitions found.</Typography>
        </Paper>
      )}

      <Stack spacing={3}>
        {pipelines.map((p) => (
          <Paper key={p.name} variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
              <Typography variant="h6" fontWeight={700}>
                {p.name}
              </Typography>
              <Chip label={`v${p.version}`} size="small" variant="outlined" />
              <Chip label={`${p.stages.length} stages`} size="small" />
              {p.stages.some((s) => s.conditions.length > 0) && (
                <Tooltip title="Has conditional transitions">
                  <Chip label="conditional" size="small" color="secondary" variant="outlined" />
                </Tooltip>
              )}
            </Stack>
            <PipelineFlow pipeline={p} uid={p.name.replace(/[^a-z0-9]/g, '')} />
            <ConditionsSummary stages={p.stages} />
          </Paper>
        ))}
      </Stack>
    </Box>
  )
}
