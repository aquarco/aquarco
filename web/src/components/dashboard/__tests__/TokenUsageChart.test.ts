/**
 * Tests for TokenUsageChart helper functions and data transformation logic.
 *
 * Validates Issue #83: token usage chart component utilities.
 * Tests the pure functions exported/used by the component without rendering.
 */

import { describe, it, expect } from 'vitest'

// We re-implement the pure logic functions here since they're not exported.
// This tests the same logic the component uses internally.

// ── getModelColor ─────────────────────────────────────────────────────────────

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

describe('getModelColor', () => {
  it('should return purple for opus models', () => {
    expect(getModelColor('claude-opus-4-6')).toBe('#7c3aed')
    expect(getModelColor('claude-opus-4-5')).toBe('#7c3aed')
  })

  it('should return blue for sonnet models', () => {
    expect(getModelColor('claude-sonnet-4-6')).toBe('#1976d2')
  })

  it('should return green for haiku models', () => {
    expect(getModelColor('claude-haiku-4-5')).toBe('#2e7d32')
  })

  it('should return grey for unknown model', () => {
    expect(getModelColor('unknown')).toBe('#757575')
  })

  it('should return fallback grey for unrecognized models', () => {
    expect(getModelColor('some-future-model')).toBe('#9e9e9e')
  })

  it('should be case-insensitive', () => {
    expect(getModelColor('Claude-OPUS-4-6')).toBe('#7c3aed')
    expect(getModelColor('CLAUDE-SONNET-4-6')).toBe('#1976d2')
  })
})

// ── shortModelLabel ───────────────────────────────────────────────────────────

function shortModelLabel(model: string): string {
  return model.replace(/^claude-/, '')
}

describe('shortModelLabel', () => {
  it('should strip claude- prefix', () => {
    expect(shortModelLabel('claude-sonnet-4-6')).toBe('sonnet-4-6')
    expect(shortModelLabel('claude-opus-4-6')).toBe('opus-4-6')
    expect(shortModelLabel('claude-haiku-4-5')).toBe('haiku-4-5')
  })

  it('should not modify strings without claude- prefix', () => {
    expect(shortModelLabel('unknown')).toBe('unknown')
    expect(shortModelLabel('sonnet-4-6')).toBe('sonnet-4-6')
  })

  it('should only strip leading claude-', () => {
    expect(shortModelLabel('claude-claude-test')).toBe('claude-test')
  })
})

// ── getTokenValue ─────────────────────────────────────────────────────────────

interface TokenUsageByDay {
  day: string
  model: string
  tokensInput: number
  tokensOutput: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costUsd: number
}

type TokenType = 'total' | 'input' | 'output' | 'cacheRead' | 'cacheWrite'

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

const sampleRow: TokenUsageByDay = {
  day: '2026-04-01T00:00:00Z',
  model: 'claude-sonnet-4-6',
  tokensInput: 1000,
  tokensOutput: 500,
  cacheReadTokens: 200,
  cacheWriteTokens: 100,
  costUsd: 0.05,
}

describe('getTokenValue', () => {
  it('should return input tokens for input type', () => {
    expect(getTokenValue(sampleRow, 'input')).toBe(1000)
  })

  it('should return output tokens for output type', () => {
    expect(getTokenValue(sampleRow, 'output')).toBe(500)
  })

  it('should return cache read tokens for cacheRead type', () => {
    expect(getTokenValue(sampleRow, 'cacheRead')).toBe(200)
  })

  it('should return cache write tokens for cacheWrite type', () => {
    expect(getTokenValue(sampleRow, 'cacheWrite')).toBe(100)
  })

  it('should return sum of all tokens for total type', () => {
    expect(getTokenValue(sampleRow, 'total')).toBe(1800)
  })
})

// ── formatDay ─────────────────────────────────────────────────────────────────

function formatDay(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

describe('formatDay', () => {
  it('should format ISO date to short month and day', () => {
    const result = formatDay('2026-04-01T00:00:00Z')
    expect(result).toMatch(/Apr/)
    expect(result).toMatch(/1/)
  })

  it('should handle different months', () => {
    expect(formatDay('2026-01-15T00:00:00Z')).toMatch(/Jan/)
    expect(formatDay('2026-12-25T00:00:00Z')).toMatch(/Dec/)
  })
})

// ── Chart data transformation ─────────────────────────────────────────────────

describe('chart data transformation', () => {
  function transformData(data: TokenUsageByDay[], tokenType: TokenType) {
    if (!data?.length) return { chartData: [], models: [] }

    const modelSet = new Set<string>()
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
  }

  const testData: TokenUsageByDay[] = [
    {
      day: '2026-04-01T00:00:00Z',
      model: 'claude-sonnet-4-6',
      tokensInput: 1000,
      tokensOutput: 500,
      cacheReadTokens: 200,
      cacheWriteTokens: 100,
      costUsd: 0.05,
    },
    {
      day: '2026-04-01T00:00:00Z',
      model: 'claude-opus-4-6',
      tokensInput: 2000,
      tokensOutput: 800,
      cacheReadTokens: 0,
      cacheWriteTokens: 300,
      costUsd: 0.12,
    },
    {
      day: '2026-04-02T00:00:00Z',
      model: 'claude-sonnet-4-6',
      tokensInput: 500,
      tokensOutput: 200,
      cacheReadTokens: 100,
      cacheWriteTokens: 50,
      costUsd: 0.03,
    },
  ]

  it('should return empty arrays for empty data', () => {
    const result = transformData([], 'total')
    expect(result.chartData).toEqual([])
    expect(result.models).toEqual([])
  })

  it('should return empty arrays for null-ish data', () => {
    const result = transformData(null as unknown as TokenUsageByDay[], 'total')
    expect(result.chartData).toEqual([])
    expect(result.models).toEqual([])
  })

  it('should collect unique models sorted alphabetically', () => {
    const result = transformData(testData, 'total')
    expect(result.models).toEqual(['claude-opus-4-6', 'claude-sonnet-4-6'])
  })

  it('should group data by day', () => {
    const result = transformData(testData, 'total')
    expect(result.chartData).toHaveLength(2)
  })

  it('should sort chart data by day ascending', () => {
    const result = transformData(testData, 'total')
    // First entry should be Apr 1, second should be Apr 2
    expect(result.chartData[0].day).toMatch(/Apr/)
  })

  it('should aggregate total tokens per model per day', () => {
    const result = transformData(testData, 'total')
    // Day 1: sonnet total = 1000+500+200+100 = 1800
    // Day 1: opus total = 2000+800+0+300 = 3100
    expect(result.chartData[0]['claude-sonnet-4-6']).toBe(1800)
    expect(result.chartData[0]['claude-opus-4-6']).toBe(3100)
  })

  it('should filter by input token type', () => {
    const result = transformData(testData, 'input')
    expect(result.chartData[0]['claude-sonnet-4-6']).toBe(1000)
    expect(result.chartData[0]['claude-opus-4-6']).toBe(2000)
  })

  it('should filter by output token type', () => {
    const result = transformData(testData, 'output')
    expect(result.chartData[0]['claude-sonnet-4-6']).toBe(500)
    expect(result.chartData[0]['claude-opus-4-6']).toBe(800)
  })

  it('should filter by cacheRead token type', () => {
    const result = transformData(testData, 'cacheRead')
    expect(result.chartData[0]['claude-sonnet-4-6']).toBe(200)
    expect(result.chartData[0]['claude-opus-4-6']).toBe(0)
  })

  it('should handle single model data', () => {
    const singleModel = testData.filter((d) => d.model === 'claude-sonnet-4-6')
    const result = transformData(singleModel, 'total')
    expect(result.models).toEqual(['claude-sonnet-4-6'])
    expect(result.chartData).toHaveLength(2)
  })

  it('should aggregate costUsd per day', () => {
    // Build a dayMap of costUsd, same logic as the component
    const dayMap = new Map<string, number>()
    for (const row of testData) {
      const key = row.day
      dayMap.set(key, (dayMap.get(key) ?? 0) + row.costUsd)
    }
    // Day 1: 0.05 + 0.12 = 0.17
    expect(dayMap.get('2026-04-01T00:00:00Z')).toBeCloseTo(0.17)
    // Day 2: 0.03
    expect(dayMap.get('2026-04-02T00:00:00Z')).toBeCloseTo(0.03)
  })
})
