/**
 * Tests for tokenUsageByModel resolver — days parameter clamping (Issue #141).
 *
 * The resolver clamps the days argument: min 1, max 365, default 30.
 * Validates that the SQL interval uses the clamped value.
 */

import { jest, describe, it, expect } from '@jest/globals'
import { taskQueries } from '../resolvers/task-queries.js'
import type { Context } from '../context.js'

// ── Mock pool factory ──────────────────────────────────────────────────────────

function mockPool() {
  const query = jest.fn((..._args: unknown[]) =>
    Promise.resolve({ rows: [] })
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

// ── days parameter clamping ────────────────────────────────────────────────────

describe('tokenUsageByModel — days clamping', () => {
  it('should default to 30 days when days is null', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: null }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('30')
  })

  it('should default to 30 days when days is undefined', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, {}, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('30')
  })

  it('should clamp days to minimum of 1', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 0 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('1')
  })

  it('should clamp negative days to 1', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: -10 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('1')
  })

  it('should clamp days to maximum of 365', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 1000 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('365')
  })

  it('should pass through valid days within range', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('7')
  })

  it('should allow exactly 1 day', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 1 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('1')
  })

  it('should allow exactly 365 days', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 365 }, ctx)

    const params = pool.query.mock.calls[0][1] as string[]
    expect(params[0]).toBe('365')
  })

  it('should return empty array when no data', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result).toEqual([])
  })

  it('should map row fields correctly including costUsd', async () => {
    const query = jest.fn((..._args: unknown[]) =>
      Promise.resolve({
        rows: [
          {
            day: new Date('2026-04-10T00:00:00Z'),
            model: 'claude-sonnet-4-6',
            tokens_input: 1000,
            tokens_output: 500,
            cache_read_tokens: 200,
            cache_write_tokens: 100,
            cost_usd: 0.15,
          },
        ],
      })
    )
    const pool = { query }
    const ctx = makeCtx(pool)

    const result = await taskQueries.tokenUsageByModel(null, { days: 14 }, ctx)

    expect(result).toHaveLength(1)
    expect(result[0]).toEqual({
      day: '2026-04-10T00:00:00.000Z',
      model: 'claude-sonnet-4-6',
      tokensInput: 1000,
      tokensOutput: 500,
      cacheReadTokens: 200,
      cacheWriteTokens: 100,
      costUsd: 0.15,
    })
  })

  it('should use interval syntax in SQL query', async () => {
    const pool = mockPool()
    const ctx = makeCtx(pool)

    await taskQueries.tokenUsageByModel(null, { days: 14 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain("|| ' days')::INTERVAL")
  })
})
