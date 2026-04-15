'use client'

import { useMemo } from 'react'
import Box from '@mui/material/Box'
import Skeleton from '@mui/material/Skeleton'
import Typography from '@mui/material/Typography'
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { formatTokens, formatCost } from '@/lib/spending'

interface TokenUsageByDay {
  day: string
  model: string
  tokensInput: number
  tokensOutput: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costUsd: number
}

interface TokenUsageChartProps {
  data: TokenUsageByDay[]
  loading: boolean
  days?: number
}

const TOKEN_COLORS = {
  input: '#1976d2',
  output: '#2e7d32',
  cacheRead: '#ed6c02',
  cacheWrite: '#7c3aed',
}

const TOKEN_LABELS: Record<string, string> = {
  input: 'Input',
  output: 'Output',
  cacheRead: 'Cache Read',
  cacheWrite: 'Cache Write',
  costUsd: 'Cost (USD)',
}

const MODEL_COLORS: Record<string, string> = {
  opus: '#7c3aed',
  sonnet: '#1976d2',
  haiku: '#2e7d32',
  unknown: '#757575',
}

const FALLBACK_COLORS = ['#e91e63', '#00bcd4', '#ff9800', '#009688']

function getModelColor(model: string, index: number): string {
  const lower = model.toLowerCase()
  if (lower.includes('opus')) return MODEL_COLORS.opus
  if (lower.includes('sonnet')) return MODEL_COLORS.sonnet
  if (lower.includes('haiku')) return MODEL_COLORS.haiku
  if (lower === 'unknown') return MODEL_COLORS.unknown
  return FALLBACK_COLORS[index % FALLBACK_COLORS.length]
}

function shortModelLabel(model: string): string {
  return model.replace(/^claude-/, '')
}

function toUtcDateKey(dateStr: string): string {
  // Handles both "2026-04-01T00:00:00Z" and "2026-04-01" → "2026-04-01"
  return dateStr.slice(0, 10)
}

function formatDay(isoDate: string): string {
  // isoDate is YYYY-MM-DD; parse as UTC to avoid local-timezone shift
  const [y, m, d] = isoDate.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

function buildFullDayRange(days: number, startIsoDate: string): string[] {
  const [y, m, d] = startIsoDate.split('-').map(Number)
  const result: string[] = []
  for (let i = 0; i < days; i++) {
    const date = new Date(Date.UTC(y, m - 1, d + i))
    result.push(date.toISOString().slice(0, 10))
  }
  return result
}

export function TokenUsageChart({ data, loading, days = 30 }: TokenUsageChartProps) {
  const { chartData, donutData } = useMemo(() => {
    const dayMap = new Map<string, { input: number; output: number; cacheRead: number; cacheWrite: number; costUsd: number }>()
    const modelMap = new Map<string, number>()

    for (const row of data ?? []) {
      const key = toUtcDateKey(row.day)
      if (!dayMap.has(key)) {
        dayMap.set(key, { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 })
      }
      const entry = dayMap.get(key)!
      entry.input += row.tokensInput
      entry.output += row.tokensOutput
      entry.cacheRead += row.cacheReadTokens
      entry.cacheWrite += row.cacheWriteTokens
      entry.costUsd += row.costUsd ?? 0

      const total = row.tokensInput + row.tokensOutput + row.cacheReadTokens + row.cacheWriteTokens
      modelMap.set(row.model, (modelMap.get(row.model) ?? 0) + total)
    }

    // Always emit the full date range starting from the earliest data day,
    // so empty slots appear in the future rather than the past.
    const allKeys = Array.from(dayMap.keys()).sort()
    const startDate = allKeys[0] ?? new Date().toISOString().slice(0, 10)
    const chartData = buildFullDayRange(days, startDate).map((isoDate) => ({
      day: formatDay(isoDate),
      ...(dayMap.get(isoDate) ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 }),
    }))

    const donutData = Array.from(modelMap.entries())
      .sort(([, a], [, b]) => b - a)
      .map(([model, value]) => ({ name: shortModelLabel(model), fullName: model, value }))

    return { chartData, donutData }
  }, [data, days])

  if (loading) {
    return <Skeleton variant="rectangular" height={300} />
  }

  if (!data?.length) {
    return (
      <Box
        sx={{
          height: 300,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'text.secondary',
        }}
      >
        No token usage data available
      </Box>
    )
  }

  return (
    <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={chartData} barSize={8}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="day" tick={{ fontSize: 12 }} />
            <YAxis yAxisId="tokens" tickFormatter={(v: number) => formatTokens(v)} width={55} />
            <YAxis
              yAxisId="cost"
              orientation="right"
              tickFormatter={(v: number) => `$${v.toFixed(2)}`}
              width={60}
            />
            <Tooltip
              formatter={(value: number, name: string) => [
                name === 'costUsd' ? formatCost(value) : formatTokens(value),
                TOKEN_LABELS[name] ?? name,
              ]}
            />
            <Legend formatter={(name: string) => TOKEN_LABELS[name] ?? name} />
            <Bar yAxisId="tokens" dataKey="input" stackId="t" fill={TOKEN_COLORS.input} name="input" />
            <Bar yAxisId="tokens" dataKey="output" stackId="t" fill={TOKEN_COLORS.output} name="output" />
            <Bar yAxisId="tokens" dataKey="cacheRead" stackId="t" fill={TOKEN_COLORS.cacheRead} name="cacheRead" />
            <Bar yAxisId="tokens" dataKey="cacheWrite" stackId="t" fill={TOKEN_COLORS.cacheWrite} name="cacheWrite" />
            <Line
              yAxisId="cost"
              type="monotone"
              dataKey="costUsd"
              stroke="#f57c00"
              strokeWidth={2}
              dot={false}
              name="costUsd"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </Box>

      <Box sx={{ width: 180, flexShrink: 0 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', textAlign: 'center', mb: 0.5 }}>
          Models
        </Typography>
        <ResponsiveContainer width="100%" height={260}>
          <PieChart>
            <Pie
              data={donutData}
              cx="50%"
              cy="45%"
              innerRadius={48}
              outerRadius={72}
              dataKey="value"
              nameKey="name"
            >
              {donutData.map((entry, index) => (
                <Cell key={entry.fullName} fill={getModelColor(entry.fullName, index)} />
              ))}
            </Pie>
            <Tooltip formatter={(value: number, name: string) => [formatTokens(value), name]} />
            <Legend
              iconType="circle"
              iconSize={8}
              layout="vertical"
              align="center"
              verticalAlign="bottom"
              formatter={(name: string) => (
                <span style={{ fontSize: 11 }}>{name}</span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </Box>
    </Box>
  )
}
