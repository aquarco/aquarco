/**
 * Tests for TokenUsageChart — cost line and donut data transformation (Issue #141).
 *
 * Validates:
 * - costUsd aggregation in the full chart data pipeline (mirrors useMemo logic)
 * - Donut data model aggregation and sorting
 * - Edge cases: large costs, fractional costs, single-day data
 * - Cost Y-axis tick formatting
 */

import { describe, it, expect } from 'vitest'

// ── Re-implement pure logic from the component ────────────────────────────────

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

function shortModelLabel(model: string): string {
  return model.replace(/^claude-/, '')
}

/**
 * Mirrors the component's useMemo logic exactly
 */
function buildChartAndDonutData(data: TokenUsageByDay[], days: number) {
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
}

// ── Test data ──────────────────────────────────────────────────────────────────

const multiModelData: TokenUsageByDay[] = [
  {
    day: '2026-04-01T00:00:00Z',
    model: 'claude-opus-4-6',
    tokensInput: 5000,
    tokensOutput: 2000,
    cacheReadTokens: 1000,
    cacheWriteTokens: 500,
    costUsd: 1.50,
  },
  {
    day: '2026-04-01T00:00:00Z',
    model: 'claude-sonnet-4-6',
    tokensInput: 3000,
    tokensOutput: 1000,
    cacheReadTokens: 500,
    cacheWriteTokens: 200,
    costUsd: 0.25,
  },
  {
    day: '2026-04-01T00:00:00Z',
    model: 'claude-haiku-4-5',
    tokensInput: 1000,
    tokensOutput: 500,
    cacheReadTokens: 200,
    cacheWriteTokens: 100,
    costUsd: 0.01,
  },
  {
    day: '2026-04-02T00:00:00Z',
    model: 'claude-opus-4-6',
    tokensInput: 4000,
    tokensOutput: 1500,
    cacheReadTokens: 800,
    cacheWriteTokens: 400,
    costUsd: 1.20,
  },
  {
    day: '2026-04-02T00:00:00Z',
    model: 'claude-sonnet-4-6',
    tokensInput: 2000,
    tokensOutput: 800,
    cacheReadTokens: 300,
    cacheWriteTokens: 100,
    costUsd: 0.18,
  },
]

// ── Donut data tests ───────────────────────────────────────────────────────────

describe('donut data (model aggregation)', () => {
  it('should aggregate total tokens per model across all days', () => {
    const { donutData } = buildChartAndDonutData(multiModelData, 2)

    // opus: (5000+2000+1000+500) + (4000+1500+800+400) = 8500+6700 = 15200
    const opus = donutData.find((d) => d.fullName === 'claude-opus-4-6')
    expect(opus?.value).toBe(15200)

    // sonnet: (3000+1000+500+200) + (2000+800+300+100) = 4700+3200 = 7900
    const sonnet = donutData.find((d) => d.fullName === 'claude-sonnet-4-6')
    expect(sonnet?.value).toBe(7900)

    // haiku: 1000+500+200+100 = 1800
    const haiku = donutData.find((d) => d.fullName === 'claude-haiku-4-5')
    expect(haiku?.value).toBe(1800)
  })

  it('should sort donut data by value descending (highest first)', () => {
    const { donutData } = buildChartAndDonutData(multiModelData, 2)

    expect(donutData[0].fullName).toBe('claude-opus-4-6')
    expect(donutData[1].fullName).toBe('claude-sonnet-4-6')
    expect(donutData[2].fullName).toBe('claude-haiku-4-5')
  })

  it('should strip claude- prefix in donut labels', () => {
    const { donutData } = buildChartAndDonutData(multiModelData, 2)

    expect(donutData[0].name).toBe('opus-4-6')
    expect(donutData[1].name).toBe('sonnet-4-6')
    expect(donutData[2].name).toBe('haiku-4-5')
  })

  it('should keep fullName for color matching', () => {
    const { donutData } = buildChartAndDonutData(multiModelData, 2)

    donutData.forEach((entry) => {
      expect(entry.fullName).toMatch(/^claude-/)
    })
  })

  it('should return empty donut data for empty input', () => {
    const { donutData } = buildChartAndDonutData([], 7)
    expect(donutData).toEqual([])
  })

  it('should handle unknown model label', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'unknown',
        tokensInput: 100,
        tokensOutput: 50,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0.001,
      },
    ]
    const { donutData } = buildChartAndDonutData(data, 1)
    expect(donutData[0].name).toBe('unknown')
    expect(donutData[0].fullName).toBe('unknown')
  })
})

// ── Cost line in chart data ─────────────────────────────────────────────────

describe('cost line in chart data', () => {
  it('should aggregate costUsd across models for each day', () => {
    const { chartData } = buildChartAndDonutData(multiModelData, 2)

    // Day 1: 1.50 + 0.25 + 0.01 = 1.76
    expect(chartData[0].costUsd).toBeCloseTo(1.76)
    // Day 2: 1.20 + 0.18 = 1.38
    expect(chartData[1].costUsd).toBeCloseTo(1.38)
  })

  it('should handle large cost values', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-opus-4-6',
        tokensInput: 100000,
        tokensOutput: 50000,
        cacheReadTokens: 20000,
        cacheWriteTokens: 10000,
        costUsd: 42.50,
      },
    ]
    const { chartData } = buildChartAndDonutData(data, 1)
    expect(chartData[0].costUsd).toBe(42.50)
  })

  it('should handle very small fractional costs', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-haiku-4-5',
        tokensInput: 10,
        tokensOutput: 5,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0.000042,
      },
    ]
    const { chartData } = buildChartAndDonutData(data, 1)
    expect(chartData[0].costUsd).toBeCloseTo(0.000042, 6)
  })

  it('should zero-fill costUsd for days without data', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-sonnet-4-6',
        tokensInput: 100,
        tokensOutput: 50,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0.05,
      },
    ]
    const { chartData } = buildChartAndDonutData(data, 5)
    expect(chartData).toHaveLength(5)
    expect(chartData[0].costUsd).toBeCloseTo(0.05)
    for (let i = 1; i < 5; i++) {
      expect(chartData[i].costUsd).toBe(0)
    }
  })

  it('should handle data with costUsd of exactly 0', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-sonnet-4-6',
        tokensInput: 500,
        tokensOutput: 200,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0,
      },
    ]
    const { chartData } = buildChartAndDonutData(data, 1)
    expect(chartData[0].costUsd).toBe(0)
  })

  it('should not double-count cost when same model appears multiple times on same day', () => {
    const data: TokenUsageByDay[] = [
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-sonnet-4-6',
        tokensInput: 500,
        tokensOutput: 200,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0.10,
      },
      {
        day: '2026-04-01T00:00:00Z',
        model: 'claude-sonnet-4-6',
        tokensInput: 300,
        tokensOutput: 100,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: 0.07,
      },
    ]
    const { chartData } = buildChartAndDonutData(data, 1)
    // Should sum: 0.10 + 0.07 = 0.17
    expect(chartData[0].costUsd).toBeCloseTo(0.17)
    // Tokens should also sum: input = 500+300 = 800
    expect(chartData[0].input).toBe(800)
  })
})

// ── Cost Y-axis tick formatter ──────────────────────────────────────────────

describe('cost Y-axis tick formatting', () => {
  // The component uses: (v: number) => `$${v.toFixed(2)}`
  const costTickFormatter = (v: number) => `$${v.toFixed(2)}`

  it('should format zero as $0.00', () => {
    expect(costTickFormatter(0)).toBe('$0.00')
  })

  it('should format whole dollars with 2 decimals', () => {
    expect(costTickFormatter(5)).toBe('$5.00')
  })

  it('should format fractional amounts with 2 decimals', () => {
    expect(costTickFormatter(1.5)).toBe('$1.50')
    expect(costTickFormatter(0.99)).toBe('$0.99')
  })

  it('should round to 2 decimal places', () => {
    expect(costTickFormatter(1.999)).toBe('$2.00')
    expect(costTickFormatter(0.005)).toBe('$0.01')
  })
})

// ── Tooltip formatter logic ─────────────────────────────────────────────────

describe('tooltip formatter logic', () => {
  // Mirrors the component's Tooltip formatter
  const TOKEN_LABELS: Record<string, string> = {
    input: 'Input',
    output: 'Output',
    cacheRead: 'Cache Read',
    cacheWrite: 'Cache Write',
    costUsd: 'Cost (USD)',
  }

  // Uses formatCost for cost, formatTokens for tokens
  function formatCost(usd: number | null | undefined): string {
    if (usd == null || usd === 0) return '—'
    if (usd < 0.01) return `$${usd.toFixed(4)}`
    return `$${usd.toFixed(2)}`
  }

  function formatTokens(count: number | null | undefined): string {
    if (count == null || count === 0) return '—'
    if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
    if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
    return String(count)
  }

  function tooltipFormatter(value: number, name: string): [string, string] {
    return [
      name === 'costUsd' ? formatCost(value) : formatTokens(value),
      TOKEN_LABELS[name] ?? name,
    ]
  }

  it('should format cost values using formatCost', () => {
    const [formatted, label] = tooltipFormatter(1.50, 'costUsd')
    expect(formatted).toBe('$1.50')
    expect(label).toBe('Cost (USD)')
  })

  it('should format token values using formatTokens', () => {
    const [formatted, label] = tooltipFormatter(15000, 'input')
    expect(formatted).toBe('15.0k')
    expect(label).toBe('Input')
  })

  it('should return em-dash for zero costUsd in tooltip', () => {
    const [formatted] = tooltipFormatter(0, 'costUsd')
    expect(formatted).toBe('—')
  })

  it('should return em-dash for zero tokens in tooltip', () => {
    const [formatted] = tooltipFormatter(0, 'input')
    expect(formatted).toBe('—')
  })

  it('should handle unknown name by returning name as label', () => {
    const [, label] = tooltipFormatter(100, 'unknownSeries')
    expect(label).toBe('unknownSeries')
  })
})
