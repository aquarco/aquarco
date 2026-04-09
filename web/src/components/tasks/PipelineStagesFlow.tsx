'use client'

import React from 'react'
import { useQuery } from '@apollo/client'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Typography from '@mui/material/Typography'
import { useTheme } from '@mui/material/styles'
import { GET_PIPELINE_DEFINITIONS } from '@/lib/graphql/queries'
import type { Stage, PipelineCondition, PipelineStageDefn } from '@/app/tasks/[id]/types'

export type { PipelineCondition, PipelineStageDefn }

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

interface FlowEdge {
  from: number
  to: number
  label: string
  condType: string
  isSelf: boolean
}

interface PipelineStagesFlowProps {
  stages: Stage[]  // deduplicated: one entry per unique stageNumber (latest run wins)
  activeStep: number
  pipelineName: string
  effectiveExecutingStages: Set<number>
}

export function PipelineStagesFlow({
  stages,
  activeStep,
  pipelineName,
  effectiveExecutingStages,
}: PipelineStagesFlowProps) {
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

export default PipelineStagesFlow
