/**
 * Tests for dashboardStats resolver — totalCostToday field.
 *
 * Validates Issue #141: cost spending visualisation — the dashboard stats
 * query now includes a totalCostToday field aggregated from stages.cost_usd.
 */

import { jest, describe, it, expect } from '@jest/globals'
import { taskQueries } from '../resolvers/task-queries.js'
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

// ── Standard mock responses for the 6 parallel queries in dashboardStats ─────

function makeDashboardResponses(overrides?: {
  totals?: Record<string, unknown>
  cost?: Record<string, unknown>
  tokens?: Record<string, unknown>
}) {
  return [
    // 1. totals
    {
      rows: [
        overrides?.totals ?? {
          total: '42',
          pending: '5',
          executing: '3',
          completed: '30',
          failed: '2',
          blocked: '2',
        },
      ],
    },
    // 2. tasksByPipeline
    { rows: [{ pipeline: 'feature-pipeline', count: '35' }] },
    // 3. tasksByRepository
    { rows: [{ repository: 'aquarco', count: '42' }] },
    // 4. agents
    { rows: [{ count: '1' }] },
    // 5. tokens today
    { rows: [overrides?.tokens ?? { total: '50000' }] },
    // 6. cost today
    { rows: [overrides?.cost ?? { total: '1.75' }] },
  ]
}

// ── dashboardStats: totalCostToday ──────────────────────────────────────────

describe('dashboardStats — totalCostToday', () => {
  it('should return totalCostToday as a float', async () => {
    const pool = mockPool(makeDashboardResponses({ cost: { total: '3.42' } }))
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result.totalCostToday).toBe(3.42)
    expect(typeof result.totalCostToday).toBe('number')
  })

  it('should return 0 when no stages have cost today', async () => {
    const pool = mockPool(makeDashboardResponses({ cost: { total: '0' } }))
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result.totalCostToday).toBe(0)
  })

  it('should handle small fractional costs', async () => {
    const pool = mockPool(makeDashboardResponses({ cost: { total: '0.0012' } }))
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result.totalCostToday).toBeCloseTo(0.0012)
  })

  it('should include cost query with SUM(cost_usd) in SQL', async () => {
    const pool = mockPool(makeDashboardResponses())
    const ctx = makeCtx(pool)

    await taskQueries.dashboardStats(null, null, ctx)

    // The 6th query (index 5) is the cost query
    const costSql = pool.query.mock.calls[5][0] as string
    expect(costSql).toContain('SUM(cost_usd)')
    expect(costSql).toContain('CURRENT_DATE')
  })

  it('should return all expected stat fields alongside totalCostToday', async () => {
    const pool = mockPool(makeDashboardResponses())
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result).toMatchObject({
      totalTasks: 42,
      pendingTasks: 5,
      executingTasks: 3,
      completedTasks: 30,
      failedTasks: 2,
      blockedTasks: 2,
      activeAgents: 1,
      totalTokensToday: 50000,
      totalCostToday: 1.75,
    })
  })

  it('should return tasksByPipeline array', async () => {
    const pool = mockPool(makeDashboardResponses())
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result.tasksByPipeline).toEqual([
      { pipeline: 'feature-pipeline', count: 35 },
    ])
  })

  it('should return tasksByRepository array', async () => {
    const pool = mockPool(makeDashboardResponses())
    const ctx = makeCtx(pool)

    const result = await taskQueries.dashboardStats(null, null, ctx)

    expect(result.tasksByRepository).toEqual([
      { repository: 'aquarco', count: 42 },
    ])
  })

  it('should issue exactly 6 parallel queries', async () => {
    const pool = mockPool(makeDashboardResponses())
    const ctx = makeCtx(pool)

    await taskQueries.dashboardStats(null, null, ctx)

    expect(pool.query).toHaveBeenCalledTimes(6)
  })
})
