/**
 * Tests for the tokenUsageByModel GraphQL resolver and mapStage model field.
 *
 * Validates Issue #83: token usage chart data query and model field on stages.
 */

import { jest } from '@jest/globals'
import { Query, mapStage } from '../resolvers/queries.js'
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

// ── mapStage: model field ─────────────────────────────────────────────────────

describe('mapStage model field', () => {
  const baseRow: Record<string, unknown> = {
    id: 'stage-1',
    task_id: 'task-42',
    stage_number: 0,
    category: 'analyze',
    agent: 'analyze-agent',
    agent_version: '1.0.0',
    status: 'completed',
    started_at: '2026-01-01T10:00:00Z',
    completed_at: '2026-01-01T10:05:00Z',
    structured_output: null,
    raw_output: null,
    tokens_input: 1000,
    tokens_output: 500,
    cost_usd: 0.05,
    cache_read_tokens: 200,
    cache_write_tokens: 100,
    model: 'claude-sonnet-4-6',
    error_message: null,
    retry_count: 0,
    live_output: null,
  }

  it('should include model in mapped output', () => {
    const result = mapStage(baseRow)
    expect(result.model).toBe('claude-sonnet-4-6')
  })

  it('should set model to null when missing', () => {
    const result = mapStage({ ...baseRow, model: undefined })
    expect(result.model).toBeNull()
  })

  it('should set model to null when explicitly null', () => {
    const result = mapStage({ ...baseRow, model: null })
    expect(result.model).toBeNull()
  })

  it('should preserve model value for different model names', () => {
    expect(mapStage({ ...baseRow, model: 'claude-opus-4-6' }).model).toBe('claude-opus-4-6')
    expect(mapStage({ ...baseRow, model: 'claude-haiku-4-5' }).model).toBe('claude-haiku-4-5')
    expect(mapStage({ ...baseRow, model: 'unknown' }).model).toBe('unknown')
  })

  it('should include cacheReadTokens and cacheWriteTokens in mapped output', () => {
    const result = mapStage(baseRow)
    expect(result.cacheReadTokens).toBe(200)
    expect(result.cacheWriteTokens).toBe(100)
  })

  it('should null-coalesce cache token fields', () => {
    const result = mapStage({ ...baseRow, cache_read_tokens: undefined, cache_write_tokens: undefined })
    expect(result.cacheReadTokens).toBeNull()
    expect(result.cacheWriteTokens).toBeNull()
  })
})

// ── Query.tokenUsageByModel ───────────────────────────────────────────────────

describe('Query.tokenUsageByModel', () => {
  it('should return mapped token usage rows', async () => {
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
            cost_usd: 0.12,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 7 }, ctx)

    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({
      day: '2026-04-01T00:00:00.000Z',
      model: 'claude-sonnet-4-6',
      tokensInput: 1000,
      tokensOutput: 500,
      cacheReadTokens: 200,
      cacheWriteTokens: 100,
      costUsd: 0.05,
    })
    expect(result[1].model).toBe('claude-opus-4-6')
    expect(result[1].tokensInput).toBe(2000)
    expect(result[1].costUsd).toBe(0.12)
  })

  it('should default days to 30 when not provided', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, {}, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('30')
  })

  it('should default days to 30 when null', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: null }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('30')
  })

  it('should clamp days to minimum 1', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: -5 }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('1')
  })

  it('should clamp days to maximum 365', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 999999 }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('365')
  })

  it('should pass days=0 as 1 (clamped minimum)', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 0 }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('1')
  })

  it('should return empty array when no data', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 30 }, ctx)
    expect(result).toEqual([])
  })

  it('should query with parameterized interval', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 14 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('$1')
    expect(sql).toContain('INTERVAL')
    expect(sql).toContain('GROUP BY')
    expect(sql).toContain('ORDER BY')
  })

  it('should include COALESCE(SUM(cost_usd), 0) in query', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 30 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('COALESCE(SUM(cost_usd), 0)')
  })

  it('should COALESCE model to unknown in query', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.tokenUsageByModel(null, { days: 30 }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain("COALESCE(model, 'unknown')")
  })

  it('should format day as ISO string', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            day: new Date('2026-03-15T00:00:00Z'),
            model: 'unknown',
            tokens_input: 0,
            tokens_output: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cost_usd: 0,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tokenUsageByModel(null, { days: 30 }, ctx)
    expect(result[0].day).toBe('2026-03-15T00:00:00.000Z')
  })
})
