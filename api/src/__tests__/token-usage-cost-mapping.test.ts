/**
 * Tests for tokenUsageByModel resolver — costUsd field mapping edge cases (Issue #141).
 *
 * Validates:
 * - Multiple rows with varying costUsd values are mapped correctly
 * - costUsd: 0 is preserved (not null-coalesced away)
 * - SQL includes cost_usd column in SELECT and GROUP BY aggregation
 * - Row-to-GraphQL mapping converts snake_case to camelCase for costUsd
 */

import { jest, describe, it, expect } from '@jest/globals'
import { taskQueries } from '../resolvers/task-queries.js'
import type { Context } from '../context.js'

// ── Mock pool factory ──────────────────────────────────────────────────────────

function mockPool(rows: Record<string, unknown>[] = []) {
  const query = jest.fn((..._args: unknown[]) =>
    Promise.resolve({ rows })
  )
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

// ── costUsd mapping tests ─────────────────────────────────────────────────────

describe('tokenUsageByModel — costUsd mapping', () => {
  it('should map multiple rows with different costUsd values', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-opus-4-6',
        tokens_input: 5000,
        tokens_output: 2000,
        cache_read_tokens: 1000,
        cache_write_tokens: 500,
        cost_usd: 1.75,
      },
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-haiku-4-5',
        tokens_input: 500,
        tokens_output: 200,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0.003,
      },
      {
        day: new Date('2026-04-11T00:00:00Z'),
        model: 'claude-opus-4-6',
        tokens_input: 3000,
        tokens_output: 1000,
        cache_read_tokens: 500,
        cache_write_tokens: 200,
        cost_usd: 1.20,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result).toHaveLength(3)
    expect(result[0].costUsd).toBe(1.75)
    expect(result[1].costUsd).toBe(0.003)
    expect(result[2].costUsd).toBe(1.20)
  })

  it('should preserve costUsd of exactly 0', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-sonnet-4-6',
        tokens_input: 100,
        tokens_output: 50,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result[0].costUsd).toBe(0)
    expect(result[0].costUsd).not.toBeNull()
    expect(result[0].costUsd).not.toBeUndefined()
  })

  it('should handle very large costUsd values', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-opus-4-6',
        tokens_input: 1000000,
        tokens_output: 500000,
        cache_read_tokens: 200000,
        cache_write_tokens: 100000,
        cost_usd: 125.42,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 1 }, ctx)

    expect(result[0].costUsd).toBe(125.42)
  })

  it('should handle very small fractional costUsd values', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-haiku-4-5',
        tokens_input: 10,
        tokens_output: 5,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0.000042,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 1 }, ctx)

    expect(result[0].costUsd).toBeCloseTo(0.000042, 6)
  })

  it('should return costUsd alongside all other mapped fields', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'claude-sonnet-4-6',
        tokens_input: 1000,
        tokens_output: 500,
        cache_read_tokens: 200,
        cache_write_tokens: 100,
        cost_usd: 0.15,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)
    const row = result[0]

    // Verify all fields are present with correct types
    expect(typeof row.day).toBe('string')
    expect(typeof row.model).toBe('string')
    expect(typeof row.tokensInput).toBe('number')
    expect(typeof row.tokensOutput).toBe('number')
    expect(typeof row.cacheReadTokens).toBe('number')
    expect(typeof row.cacheWriteTokens).toBe('number')
    expect(typeof row.costUsd).toBe('number')
  })

  it('should include cost_usd in the SQL query', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('cost_usd')
    expect(sql).toContain('COALESCE(SUM(cost_usd), 0)')
  })

  it('should convert snake_case cost_usd to camelCase costUsd', async () => {
    const pool = mockPool([
      {
        day: new Date('2026-04-10T00:00:00Z'),
        model: 'test-model',
        tokens_input: 0,
        tokens_output: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0.99,
      },
    ])
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    // Should have camelCase key
    expect(result[0]).toHaveProperty('costUsd')
    // Should NOT have snake_case key
    expect(result[0]).not.toHaveProperty('cost_usd')
  })
})
