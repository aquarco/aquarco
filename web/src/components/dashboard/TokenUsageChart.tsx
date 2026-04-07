'use client'

import { useMemo, useState } from 'react'
import Box from '@mui/material/Box'
import Skeleton from '@mui/material/Skeleton'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import { formatTokens } from '@/lib/spending'

interface TokenUsageByDay {
  day: string
  model: string
  tokensInput: number
  tokensOutput: number
  cacheReadTokens: number
  cacheWriteTokens: number
}

type TokenType = 'total' | 'input' | 'output' | 'cacheRead' | 'cacheWrite'

interface TokenUsageChartProps {
  data: TokenUsageByDay[]
  loading: boolean
}

/** Model → color mapping */
const MODEL_COLORS: Record<string, string> = {
  opus: '#7c3aed',
  sonnet: '#1976d2',
  haiku: '#2e7d32',
  unknown: '#757575',
}

function getModelColor(model: string): string {
  const lower = model.toLowerCase()
  if (lower.includes('opus')) return MODEL_COLORS.opus
  if (lower.includes('sonnet')) return MODEL_COLORS.sonnet
  if (lower.includes('haiku')) return MODEL_COLORS.haiku
  if (lower === 'unknown') return MODEL_COLORS.unknown
  return '#9e9e9e'
}

/** Short label for display (e.g. "claude-sonnet-4-6" → "sonnet-4-6") */
function shortModelLabel(model: string): string {
  return model.replace(/^claude-/, '')
}

function getTokenValue(row: TokenUsageByDay, tokenType: TokenType): number {
  switch (tokenType) {
    case 'input':
      return row.tokensInput
    case 'output':
      return row.tokensOutput
    case 'cacheRead':
      return row.cacheReadTokens
    case 'cacheWrite':
      return row.cacheWriteTokens
    case 'total':
    default:
      return (
        row.tokensInput +
        row.tokensOutput +
        row.cacheReadTokens +
        row.cacheWriteTokens
      )
  }
}

function formatDay(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function TokenUsageChart({ data, loading }: TokenUsageChartProps) {
  const [tokenType, setTokenType] = useState<TokenType>('total')

  const { chartData, models } = useMemo(() => {
    if (!data?.length) return { chartData: [], models: [] }

    // Collect unique models
    const modelSet = new Set<string>()

    // Group by day
    const dayMap = new Map<string, Record<string, number>>()

    for (const row of data) {
      modelSet.add(row.model)
      const dayKey = row.day
      if (!dayMap.has(dayKey)) {
        dayMap.set(dayKey, {})
      }
      const entry = dayMap.get(dayKey)!
      const val = getTokenValue(row, tokenType)
      entry[row.model] = (entry[row.model] ?? 0) + val
    }

    const models = Array.from(modelSet).sort()
    const chartData = Array.from(dayMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, values]) => ({
        day: formatDay(day),
        ...values,
      }))

    return { chartData, models }
  }, [data, tokenType])

  if (loading) {
    return <Skeleton variant="rectangular" height={200} />
  }

  if (!chartData.length) {
    return (
      <Box
        sx={{
          height: 200,
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
    <Box>
      <Box sx={{ mb: 1, display: 'flex', justifyContent: 'flex-end' }}>
        <ToggleButtonGroup
          value={tokenType}
          exclusive
          onChange={(_, val) => val && setTokenType(val as TokenType)}
          size="small"
        >
          <ToggleButton value="total">Total</ToggleButton>
          <ToggleButton value="input">Input</ToggleButton>
          <ToggleButton value="output">Output</ToggleButton>
          <ToggleButton value="cacheRead">Cache Read</ToggleButton>
          <ToggleButton value="cacheWrite">Cache Write</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="day" tick={{ fontSize: 12 }} />
          <YAxis tickFormatter={(v: number) => formatTokens(v)} />
          <Tooltip
            formatter={(value: number, name: string) => [
              formatTokens(value),
              shortModelLabel(name),
            ]}
          />
          <Legend formatter={shortModelLabel} />
          {models.map((model) => (
            <Bar
              key={model}
              dataKey={model}
              stackId="tokens"
              fill={getModelColor(model)}
              name={model}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </Box>
  )
}
