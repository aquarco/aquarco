/**
 * Tests for the Task.totalTokens field resolver and the dashboardStats
 * totalTokensToday SQL fix (issue #82: show tokens count).
 *
 * Validates:
 *   - Task.totalTokens sums all four token columns (tokens_input, tokens_output,
 *     cache_read_tokens, cache_write_tokens) with per-column COALESCE
 *   - Returns null when sum is zero (no stages / all tokens null)
 *   - Returns the integer sum when > 0
 *   - dashboardStats SQL includes cache_read_tokens and cache_write_tokens
 */

import { jest } from '@jest/globals'
import { Task } from '../resolvers/types.js'
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

// ── Task.totalTokens ──────────────────────────────────────────────────────────

describe('Task.totalTokens', () => {
  it('should return the integer sum of all four token columns', async () => {
    const pool = mockPool([{ rows: [{ total: '12345' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalTokens({ id: 'task-1' }, undefined, ctx)

    expect(result).toBe(12345)
  })

  it('should return null when the sum is zero (no stages)', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalTokens({ id: 'task-empty' }, undefined, ctx)

    expect(result).toBeNull()
  })

  it('should query with the correct task_id parameter', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    await Task.totalTokens({ id: 'task-42' }, undefined, ctx)

    expect(pool.query).toHaveBeenCalledTimes(1)
    const [sql, params] = pool.query.mock.calls[0] as [string, string[]]
    expect(sql).toContain('task_id = $1')
    expect(params).toEqual(['task-42'])
  })

  it('should include cache_read_tokens and cache_write_tokens in the SQL', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    await Task.totalTokens({ id: 'task-1' }, undefined, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('cache_read_tokens')
    expect(sql).toContain('cache_write_tokens')
    expect(sql).toContain('tokens_input')
    expect(sql).toContain('tokens_output')
  })

  it('should use COALESCE for null safety on each token column', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    await Task.totalTokens({ id: 'task-1' }, undefined, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    // Should have individual COALESCE per column to handle nulls
    expect(sql).toContain('COALESCE(tokens_input, 0)')
    expect(sql).toContain('COALESCE(tokens_output, 0)')
    expect(sql).toContain('COALESCE(cache_read_tokens, 0)')
    expect(sql).toContain('COALESCE(cache_write_tokens, 0)')
  })

  it('should return a large token count correctly', async () => {
    const pool = mockPool([{ rows: [{ total: '5000000' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalTokens({ id: 'task-large' }, undefined, ctx)

    expect(result).toBe(5_000_000)
  })
})

// ── dashboardStats.totalTokensToday SQL fix ───────────────────────────────────

describe('Query.dashboardStats — totalTokensToday includes cache tokens', () => {
  it('should include all four token columns in the tokens SQL query', async () => {
    const pool = mockPool([
      // totals
      { rows: [{ total: '0', pending: '0', executing: '0', completed: '0', failed: '0', blocked: '0' }] },
      // byPipeline
      { rows: [] },
      // byRepo
      { rows: [] },
      // agents
      { rows: [{ count: '0' }] },
      // tokens
      { rows: [{ total: '0' }] },
      // cost
      { rows: [{ total: '0' }] },
    ])
    const ctx = makeCtx(pool)

    await Query.dashboardStats(null, null, ctx)

    // The tokens query is the 5th call (index 4)
    // Find the query that mentions tokens_input in a SUM
    const allCalls = pool.query.mock.calls.map(c => c[0] as string)
    const tokenQuery = allCalls.find(
      sql => sql.includes('tokens_input') && sql.includes('SUM')
    )

    expect(tokenQuery).toBeDefined()
    expect(tokenQuery).toContain('cache_read_tokens')
    expect(tokenQuery).toContain('cache_write_tokens')
  })

  it('should return the correct totalTokensToday value from the sum', async () => {
    const pool = mockPool([
      { rows: [{ total: '5', pending: '1', executing: '1', completed: '2', failed: '1', blocked: '0' }] },
      { rows: [{ pipeline: 'feature-pipeline', count: '5' }] },
      { rows: [{ repository: 'my-repo', count: '5' }] },
      { rows: [{ count: '1' }] },
      { rows: [{ total: '250000' }] },
      { rows: [{ total: '3.50' }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.dashboardStats(null, null, ctx)

    expect(result.totalTokensToday).toBe(250000)
  })
})
