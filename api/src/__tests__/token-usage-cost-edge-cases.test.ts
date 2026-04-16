/**
 * Edge-case tests for tokenUsageByModel resolver — costUsd field (Issue #141).
 *
 * Complements token-usage-resolver.test.ts with specific edge cases for the
 * costUsd field: zero costs, mixed null costs, and multi-day aggregation.
 */

import { jest, describe, it, expect } from '@jest/globals'
import { Query } from '../resolvers/queries.js'
import type { Context } from '../context.js'

// ── Mock pool factory ──────────────────────────────────────────────────────────

function mockPool(responses: Array<{ rows: Record<string, unknown>[] }>) {
  let callIndex = 0
  const query = jest.fn((..._args: unknown[]) => {
    const response = responses[callIndex] ?? { rows: [] }
    callIndex++
    return Promise.resolve(response)
  })
  return { query }
}

function makeCtx(pool: { query: jest.Mock }): Context {
  return {
    pool: pool as unknown as Context['pool'],
    loaders: {
      repositoryLoader: { load: jest.fn() } as unknown as Context['loaders']['repositoryLoader'],
      stagesByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['stagesByTaskLoader'],
      contextByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['contextByTaskLoader'],
    },
    req: { headers: {} } as unknown as Context['req'],
  }
}

// ── costUsd edge cases ──────────────────────────────────────────────────────

describe('tokenUsageByModel — costUsd edge cases', () => {
  it('should return costUsd = 0 when COALESCE returns 0', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 500,
            tokens_output: 200,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result[0].costUsd).toBe(0)
    expect(typeof result[0].costUsd).toBe('number')
  })

  it('should preserve high-precision cost values', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-haiku-4-5',
            tokens_input: 100,
            tokens_output: 50,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0.000342,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result[0].costUsd).toBeCloseTo(0.000342, 6)
  })

  it('should handle multiple models on the same day with different costs', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 1000,
            tokens_output: 500,
            cache_read_tokens: 200,
            cache_write_tokens: 100,
            cost_usd: 0.05,
          },
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-opus-4-6',
            tokens_input: 2000,
            tokens_output: 800,
            cache_read_tokens: 0,
            cache_write_tokens: 300,
            cost_usd: 0.42,
          },
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-haiku-4-5',
            tokens_input: 500,
            tokens_output: 200,
            cache_read_tokens: 100,
            cache_write_tokens: 50,
            cost_usd: 0.001,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result).toHaveLength(3)
    expect(result[0].costUsd).toBe(0.05)
    expect(result[1].costUsd).toBe(0.42)
    expect(result[2].costUsd).toBe(0.001)
  })

  it('should handle multi-day data with costs on some days only', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-04-01T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 1000,
            tokens_output: 500,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0.10,
          },
          {
            day: new Date('2026-04-02T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 2000,
            tokens_output: 800,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0,
          },
          {
            day: new Date('2026-04-03T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 500,
            tokens_output: 200,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0.25,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result).toHaveLength(3)
    expect(result[0].costUsd).toBe(0.10)
    expect(result[1].costUsd).toBe(0)
    expect(result[2].costUsd).toBe(0.25)
  })

  it('should return costUsd alongside all other mapped fields', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-04-10T00:00:00Z'),
            model: 'claude-opus-4-6',
            tokens_input: 5000,
            tokens_output: 2000,
            cache_read_tokens: 1000,
            cache_write_tokens: 500,
            cost_usd: 1.23,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 14 }, ctx)

    expect(result[0]).toEqual({
      day: '2026-04-10T00:00:00.000Z',
      model: 'claude-opus-4-6',
      tokensInput: 5000,
      tokensOutput: 2000,
      cacheReadTokens: 1000,
      cacheWriteTokens: 500,
      costUsd: 1.23,
    })
  })

  it('should include cost_usd in the SQL query', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('cost_usd')
    expect(sql).toContain('COALESCE(SUM(cost_usd), 0)')
    expect(sql).toContain('::float')
  })
})
