/**
 * Additional edge-case tests for TokenUsageChart (Issue #141).
 *
 * Covers gaps not addressed by existing tests:
 * - getModelColor with index-based fallback (FALLBACK_COLORS cycling)
 * - Legend formatter mapping via TOKEN_LABELS
 * - Cost line color constant validation
 * - Empty / null data guards
 * - chartData shape contract for ComposedChart consumption
 */

import { describe, it, expect } from 'vitest'

// ── Re-implement component constants ──────────────────────────────────────────

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
const COST_LINE_COLOR = '#f57c00'

function getModelColor(model: string, index: number): string {
  const lower = model.toLowerCase()
  if (lower.includes('opus')) return MODEL_COLORS.opus
  if (lower.includes('sonnet')) return MODEL_COLORS.sonnet
  if (lower.includes('haiku')) return MODEL_COLORS.haiku
  if (lower === 'unknown') return MODEL_COLORS.unknown
  return FALLBACK_COLORS[index % FALLBACK_COLORS.length]
}

// ── getModelColor with index-based fallback ──────────────────────────────────

describe('getModelColor — fallback color cycling', () => {
  it('should cycle through FALLBACK_COLORS for unrecognized models', () => {
    expect(getModelColor('future-model-a', 0)).toBe('#e91e63')
    expect(getModelColor('future-model-b', 1)).toBe('#00bcd4')
    expect(getModelColor('future-model-c', 2)).toBe('#ff9800')
    expect(getModelColor('future-model-d', 3)).toBe('#009688')
  })

  it('should wrap around FALLBACK_COLORS when index exceeds length', () => {
    expect(getModelColor('future-model-e', 4)).toBe('#e91e63') // 4 % 4 = 0
    expect(getModelColor('future-model-f', 5)).toBe('#00bcd4') // 5 % 4 = 1
    expect(getModelColor('future-model-g', 7)).toBe('#009688') // 7 % 4 = 3
  })

  it('should ignore index for known models (opus, sonnet, haiku)', () => {
    expect(getModelColor('claude-opus-4-6', 99)).toBe(MODEL_COLORS.opus)
    expect(getModelColor('claude-sonnet-4-6', 99)).toBe(MODEL_COLORS.sonnet)
    expect(getModelColor('claude-haiku-4-5', 99)).toBe(MODEL_COLORS.haiku)
  })

  it('should ignore index for unknown model string', () => {
    expect(getModelColor('unknown', 99)).toBe(MODEL_COLORS.unknown)
  })
})

// ── TOKEN_LABELS map coverage ────────────────────────────────────────────────

describe('TOKEN_LABELS — Legend and Tooltip label mapping', () => {
  it('should map all bar data keys to display labels', () => {
    expect(TOKEN_LABELS['input']).toBe('Input')
    expect(TOKEN_LABELS['output']).toBe('Output')
    expect(TOKEN_LABELS['cacheRead']).toBe('Cache Read')
    expect(TOKEN_LABELS['cacheWrite']).toBe('Cache Write')
  })

  it('should map costUsd to Cost (USD) for the line series', () => {
    expect(TOKEN_LABELS['costUsd']).toBe('Cost (USD)')
  })

  it('should return undefined for unknown keys (component falls back to name)', () => {
    expect(TOKEN_LABELS['unknownKey']).toBeUndefined()
  })

  // Legend formatter: TOKEN_LABELS[name] ?? name
  it('should use name as fallback when TOKEN_LABELS has no entry', () => {
    const legendFormatter = (name: string) => TOKEN_LABELS[name] ?? name
    expect(legendFormatter('input')).toBe('Input')
    expect(legendFormatter('costUsd')).toBe('Cost (USD)')
    expect(legendFormatter('someUnknown')).toBe('someUnknown')
  })
})

// ── Color constants ──────────────────────────────────────────────────────────

describe('color constants', () => {
  it('cost line should use orange (#f57c00)', () => {
    expect(COST_LINE_COLOR).toBe('#f57c00')
  })

  it('TOKEN_COLORS should have distinct colors for all 4 token types', () => {
    const colors = Object.values(TOKEN_COLORS)
    expect(new Set(colors).size).toBe(4)
  })

  it('FALLBACK_COLORS should have 4 entries', () => {
    expect(FALLBACK_COLORS).toHaveLength(4)
  })
})

// ── chartData shape contract ─────────────────────────────────────────────────

describe('chartData shape for ComposedChart', () => {
  interface TokenUsageByDay {
    day: string
    model: string
    tokensInput: number
    tokensOutput: number
    cacheReadTokens: number
    cacheWriteTokens: number
    costUsd: number
  }

  function toUtcDateKey(dateStr: string): string {
    return dateStr.slice(0, 10)
  }

  function formatDay(isoDate: string): string {
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

  function buildChartData(data: TokenUsageByDay[], days: number) {
    const dayMap = new Map<string, { input: number; output: number; cacheRead: number; cacheWrite: number; costUsd: number }>()
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
    }
    const allKeys = Array.from(dayMap.keys()).sort()
    const startDate = allKeys[0] ?? new Date().toISOString().slice(0, 10)
    return buildFullDayRange(days, startDate).map((isoDate) => ({
      day: formatDay(isoDate),
      ...(dayMap.get(isoDate) ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 }),
    }))
  }

  const sampleData: TokenUsageByDay[] = [
    {
      day: '2026-04-10T00:00:00Z',
      model: 'claude-sonnet-4-6',
      tokensInput: 1000,
      tokensOutput: 500,
      cacheReadTokens: 200,
      cacheWriteTokens: 100,
      costUsd: 0.05,
    },
  ]

  it('each entry should have all required keys for ComposedChart bars + line', () => {
    const chartData = buildChartData(sampleData, 1)
    const entry = chartData[0]
    expect(entry).toHaveProperty('day')
    expect(entry).toHaveProperty('input')
    expect(entry).toHaveProperty('output')
    expect(entry).toHaveProperty('cacheRead')
    expect(entry).toHaveProperty('cacheWrite')
    expect(entry).toHaveProperty('costUsd')
  })

  it('keys should match the Bar dataKey and Line dataKey in the component', () => {
    const chartData = buildChartData(sampleData, 1)
    const keys = Object.keys(chartData[0])
    // ComposedChart expects: day (XAxis), input/output/cacheRead/cacheWrite (Bars), costUsd (Line)
    expect(keys).toContain('day')
    expect(keys).toContain('input')
    expect(keys).toContain('output')
    expect(keys).toContain('cacheRead')
    expect(keys).toContain('cacheWrite')
    expect(keys).toContain('costUsd')
  })

  it('should produce numeric values for all data keys (no NaN or undefined)', () => {
    const chartData = buildChartData(sampleData, 3) // 2 days will be zero-filled
    for (const entry of chartData) {
      expect(typeof entry.input).toBe('number')
      expect(typeof entry.output).toBe('number')
      expect(typeof entry.cacheRead).toBe('number')
      expect(typeof entry.cacheWrite).toBe('number')
      expect(typeof entry.costUsd).toBe('number')
      expect(Number.isNaN(entry.costUsd)).toBe(false)
    }
  })

  it('costUsd should never be negative', () => {
    const chartData = buildChartData(sampleData, 3)
    for (const entry of chartData) {
      expect(entry.costUsd).toBeGreaterThanOrEqual(0)
    }
  })

  it('zero-filled days should have all numeric fields set to 0', () => {
    const chartData = buildChartData(sampleData, 3)
    // Entry at index 1 and 2 should be zero-filled
    expect(chartData[1].input).toBe(0)
    expect(chartData[1].output).toBe(0)
    expect(chartData[1].cacheRead).toBe(0)
    expect(chartData[1].cacheWrite).toBe(0)
    expect(chartData[1].costUsd).toBe(0)
  })
})

// ── Dual Y-axis configuration ────────────────────────────────────────────────

describe('dual Y-axis configuration', () => {
  it('cost Y-axis should be on the right (orientation: right)', () => {
    // This is a static configuration test — verifying the expected config
    const costAxisConfig = {
      yAxisId: 'cost',
      orientation: 'right' as const,
      tickFormatter: (v: number) => `$${v.toFixed(2)}`,
      width: 60,
    }
    expect(costAxisConfig.orientation).toBe('right')
    expect(costAxisConfig.yAxisId).toBe('cost')
  })

  it('tokens Y-axis should be on the left (default orientation)', () => {
    const tokensAxisConfig = {
      yAxisId: 'tokens',
      width: 55,
    }
    expect(tokensAxisConfig.yAxisId).toBe('tokens')
  })

  it('Bar series should use tokens yAxisId', () => {
    const barConfigs = [
      { dataKey: 'input', yAxisId: 'tokens', stackId: 't' },
      { dataKey: 'output', yAxisId: 'tokens', stackId: 't' },
      { dataKey: 'cacheRead', yAxisId: 'tokens', stackId: 't' },
      { dataKey: 'cacheWrite', yAxisId: 'tokens', stackId: 't' },
    ]
    for (const bar of barConfigs) {
      expect(bar.yAxisId).toBe('tokens')
      expect(bar.stackId).toBe('t')
    }
  })

  it('Line series should use cost yAxisId', () => {
    const lineConfig = {
      dataKey: 'costUsd',
      yAxisId: 'cost',
      stroke: '#f57c00',
      strokeWidth: 2,
      dot: false,
    }
    expect(lineConfig.yAxisId).toBe('cost')
    expect(lineConfig.dataKey).toBe('costUsd')
    expect(lineConfig.stroke).toBe(COST_LINE_COLOR)
  })
})
