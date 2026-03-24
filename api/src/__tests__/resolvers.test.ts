/**
 * Tests for GraphQL query resolvers (api/src/resolvers/queries.ts)
 *
 * All PostgreSQL pool interactions are mocked so no real database is needed.
 * The mock is typed to match the subset of pg.Pool used by the resolvers.
 */

import { jest, describe, it, expect } from '@jest/globals'
import { Query, mapRepository, mapStage } from '../resolvers/queries.js'
import type { Context } from '../context.js'

// ── Mock pool factory ──────────────────────────────────────────────────────────

/** Build a minimal mock pg.Pool whose .query() resolves to the provided rows. */
function mockPool(responses: Array<{ rows: Record<string, unknown>[] }>) {
  const calls: unknown[][] = []
  let callIndex = 0

  const query = jest.fn((...args: unknown[]) => {
    calls.push(args)
    const response = responses[callIndex] ?? { rows: [] }
    callIndex++
    return Promise.resolve(response)
  })

  return { query, calls }
}

function makeCtx(pool: { query: jest.Mock }): Context {
  return {
    pool: pool as unknown as Context['pool'],
    loaders: {
      repositoryLoader: { load: jest.fn() } as unknown as Context['loaders']['repositoryLoader'],
      stagesByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['stagesByTaskLoader'],
      contextByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['contextByTaskLoader'],
    },
  }
}

// ── mapRepository ──────────────────────────────────────────────────────────────

describe('mapRepository', () => {
  const baseRow: Record<string, unknown> = {
    name: 'my-repo',
    url: 'https://github.com/org/my-repo',
    branch: 'main',
    clone_dir: '/repos/my-repo',
    pollers: ['github-issues'],
    last_cloned_at: '2026-01-01T00:00:00Z',
    last_pulled_at: '2026-01-02T00:00:00Z',
    clone_status: 'cloned',
    head_sha: 'abc123',
  }

  it('should map all snake_case DB fields to camelCase GraphQL shape', () => {
    const result = mapRepository(baseRow)
    expect(result.name).toBe('my-repo')
    expect(result.cloneDir).toBe('/repos/my-repo')
    expect(result.lastClonedAt).toBe('2026-01-01T00:00:00Z')
    expect(result.lastPulledAt).toBe('2026-01-02T00:00:00Z')
    expect(result.headSha).toBe('abc123')
  })

  it('should uppercase clone_status', () => {
    const result = mapRepository(baseRow)
    expect(result.cloneStatus).toBe('CLONED')
  })

  it('should set _name for DataLoader resolution', () => {
    const result = mapRepository(baseRow)
    expect(result._name).toBe('my-repo')
  })

  it('should default pollers to empty array when null', () => {
    const result = mapRepository({ ...baseRow, pollers: null })
    expect(result.pollers).toEqual([])
  })

  it('should set lastClonedAt and lastPulledAt to null when missing', () => {
    const result = mapRepository({ ...baseRow, last_cloned_at: null, last_pulled_at: null })
    expect(result.lastClonedAt).toBeNull()
    expect(result.lastPulledAt).toBeNull()
  })

  it('should set headSha to null when missing', () => {
    const result = mapRepository({ ...baseRow, head_sha: undefined })
    expect(result.headSha).toBeNull()
  })
})

// ── mapStage ───────────────────────────────────────────────────────────────────

describe('mapStage', () => {
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
    structured_output: { result: 'ok' },
    raw_output: 'raw text',
    tokens_input: 1000,
    tokens_output: 500,
    error_message: null,
    retry_count: 0,
  }

  it('should uppercase category and status', () => {
    const result = mapStage(baseRow)
    expect(result.category).toBe('ANALYZE')
    expect(result.status).toBe('COMPLETED')
  })

  it('should map all fields to camelCase', () => {
    const result = mapStage(baseRow)
    expect(result.taskId).toBe('task-42')
    expect(result.stageNumber).toBe(0)
    expect(result.agentVersion).toBe('1.0.0')
    expect(result.startedAt).toBe('2026-01-01T10:00:00Z')
    expect(result.completedAt).toBe('2026-01-01T10:05:00Z')
    expect(result.tokensInput).toBe(1000)
    expect(result.tokensOutput).toBe(500)
    expect(result.retryCount).toBe(0)
  })

  it('should null-coalesce optional fields', () => {
    const result = mapStage({
      ...baseRow,
      agent: undefined,
      agent_version: undefined,
      started_at: undefined,
      completed_at: undefined,
      structured_output: undefined,
      raw_output: undefined,
      tokens_input: undefined,
      tokens_output: undefined,
      error_message: undefined,
    })
    expect(result.agent).toBeNull()
    expect(result.agentVersion).toBeNull()
    expect(result.startedAt).toBeNull()
    expect(result.completedAt).toBeNull()
    expect(result.structuredOutput).toBeNull()
    expect(result.rawOutput).toBeNull()
    expect(result.tokensInput).toBeNull()
    expect(result.tokensOutput).toBeNull()
    expect(result.errorMessage).toBeNull()
  })
})

// ── Query.task ─────────────────────────────────────────────────────────────────

describe('Query.task', () => {
  const dbRow: Record<string, unknown> = {
    id: 'task-1',
    title: 'Fix bug',
    status: 'pending',
    priority: 5,
    source: 'github-issue',
    source_ref: '42',
    pipeline: 'bugfix-pipeline',
    repository: 'my-repo',
    initial_context: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    started_at: null,
    completed_at: null,
    assigned_agent: null,
    current_stage: 0,
    retry_count: 0,
    error_message: null,
  }

  it('should return mapped task when found', async () => {
    const pool = mockPool([{ rows: [dbRow] }])
    const ctx = makeCtx(pool)

    const result = await Query.task(null, { id: 'task-1' }, ctx)

    expect(result).not.toBeNull()
    expect(result!.id).toBe('task-1')
    expect(result!.status).toBe('PENDING')
    expect(result!._repositoryName).toBe('my-repo')
  })

  it('should return null when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.task(null, { id: 'nonexistent' }, ctx)
    expect(result).toBeNull()
  })

  it('should query by id parameter', async () => {
    const pool = mockPool([{ rows: [dbRow] }])
    const ctx = makeCtx(pool)

    await Query.task(null, { id: 'task-1' }, ctx)

    expect(pool.query).toHaveBeenCalledWith(
      'SELECT * FROM tasks WHERE id = $1',
      ['task-1']
    )
  })
})

// ── Query.tasks ────────────────────────────────────────────────────────────────

describe('Query.tasks', () => {
  const dbRow: Record<string, unknown> = {
    id: 'task-1',
    title: 'Fix bug',
    status: 'pending',
    priority: 5,
    source: 'github-issue',
    source_ref: '42',
    pipeline: null,
    repository: 'my-repo',
    initial_context: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    started_at: null,
    completed_at: null,
    assigned_agent: null,
    current_stage: 0,
    retry_count: 0,
    error_message: null,
  }

  it('should return nodes and totalCount', async () => {
    const pool = mockPool([
      { rows: [{ count: '1' }] },
      { rows: [dbRow] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tasks(null, {}, ctx)

    expect(result.totalCount).toBe(1)
    expect(result.nodes).toHaveLength(1)
    expect(result.nodes[0].id).toBe('task-1')
  })

  it('should apply status filter when provided', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.tasks(null, { status: 'PENDING' }, ctx)

    // First call is COUNT query — should contain status filter with lowercase DB value
    const countCallSql = pool.query.mock.calls[0][0] as string
    expect(countCallSql).toContain('status = $1')
    expect(pool.query.mock.calls[0][1]).toContain('pending')
  })

  it('should apply repository filter when provided', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.tasks(null, { repository: 'my-repo' }, ctx)

    const countCallSql = pool.query.mock.calls[0][0] as string
    expect(countCallSql).toContain('repository = $1')
    expect(pool.query.mock.calls[0][1]).toContain('my-repo')
  })

  it('should combine multiple filters with AND', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.tasks(null, { status: 'PENDING', repository: 'my-repo' }, ctx)

    const countCallSql = pool.query.mock.calls[0][0] as string
    expect(countCallSql).toContain('WHERE')
    expect(countCallSql).toContain('AND')
  })

  it('should use default limit=50 and offset=0 when not provided', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.tasks(null, {}, ctx)

    // Data query should be the second call
    const dataCallParams = pool.query.mock.calls[1][1] as unknown[]
    // Last two params are limit and offset
    expect(dataCallParams[dataCallParams.length - 2]).toBe(50)
    expect(dataCallParams[dataCallParams.length - 1]).toBe(0)
  })

  it('should respect provided limit and offset', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.tasks(null, { limit: 10, offset: 20 }, ctx)

    const dataCallParams = pool.query.mock.calls[1][1] as unknown[]
    expect(dataCallParams[dataCallParams.length - 2]).toBe(10)
    expect(dataCallParams[dataCallParams.length - 1]).toBe(20)
  })

  it('should return empty nodes and zero totalCount when no tasks exist', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.tasks(null, {}, ctx)
    expect(result.totalCount).toBe(0)
    expect(result.nodes).toHaveLength(0)
  })
})

// ── Query.repositories ─────────────────────────────────────────────────────────

describe('Query.repositories', () => {
  it('should return all repositories ordered by name', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            name: 'alpha',
            url: 'https://github.com/org/alpha',
            branch: 'main',
            clone_dir: '/repos/alpha',
            pollers: [],
            last_cloned_at: null,
            last_pulled_at: null,
            clone_status: 'pending',
            head_sha: null,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.repositories(null, null, ctx)
    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('alpha')
    expect(result[0].cloneStatus).toBe('PENDING')
  })

  it('should return empty array when no repositories exist', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)
    const result = await Query.repositories(null, null, ctx)
    expect(result).toHaveLength(0)
  })
})

// ── Query.repository ───────────────────────────────────────────────────────────

describe('Query.repository', () => {
  it('should return repository when found', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            name: 'my-repo',
            url: 'https://github.com/org/my-repo',
            branch: 'main',
            clone_dir: '/repos/my-repo',
            pollers: [],
            last_cloned_at: null,
            last_pulled_at: null,
            clone_status: 'cloned',
            head_sha: null,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)
    const result = await Query.repository(null, { name: 'my-repo' }, ctx)
    expect(result).not.toBeNull()
    expect(result!.name).toBe('my-repo')
  })

  it('should return null when repository not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)
    const result = await Query.repository(null, { name: 'missing' }, ctx)
    expect(result).toBeNull()
  })
})

// ── Query.dashboardStats ───────────────────────────────────────────────────────

describe('Query.dashboardStats', () => {
  it('should aggregate task counts from DB results', async () => {
    const pool = mockPool([
      // totals
      {
        rows: [{
          total: '10',
          pending: '3',
          executing: '2',
          completed: '4',
          failed: '1',
          blocked: '0',
        }],
      },
      // byPipeline
      { rows: [{ pipeline: 'feature-pipeline', count: '6' }, { pipeline: 'bugfix-pipeline', count: '4' }] },
      // byRepo
      { rows: [{ repository: 'my-repo', count: '10' }] },
      // agents
      { rows: [{ count: '2' }] },
      // tokens
      { rows: [{ total: '15000' }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.dashboardStats(null, null, ctx)

    expect(result.totalTasks).toBe(10)
    expect(result.pendingTasks).toBe(3)
    expect(result.executingTasks).toBe(2)
    expect(result.completedTasks).toBe(4)
    expect(result.failedTasks).toBe(1)
    expect(result.blockedTasks).toBe(0)
    expect(result.activeAgents).toBe(2)
    expect(result.totalTokensToday).toBe(15000)
  })

  it('should return pipeline counts in tasksByPipeline', async () => {
    const pool = mockPool([
      { rows: [{ total: '2', pending: '1', executing: '0', completed: '1', failed: '0', blocked: '0' }] },
      { rows: [{ pipeline: 'feature-pipeline', count: '2' }] },
      { rows: [] },
      { rows: [{ count: '0' }] },
      { rows: [{ total: '0' }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.dashboardStats(null, null, ctx)
    expect(result.tasksByPipeline[0].pipeline).toBe('feature-pipeline')
    expect(result.tasksByPipeline[0].count).toBe(2)
  })
})

// ── Query.pipelineStatus ───────────────────────────────────────────────────────

describe('Query.pipelineStatus', () => {
  const taskRow: Record<string, unknown> = {
    id: 'task-1',
    pipeline: 'feature-pipeline',
    current_stage: 1,
    status: 'executing',
  }
  const stageRow: Record<string, unknown> = {
    id: 'stage-1',
    task_id: 'task-1',
    stage_number: 0,
    category: 'analyze',
    agent: 'analyze-agent',
    agent_version: '1.0.0',
    status: 'completed',
    started_at: '2026-01-01T10:00:00Z',
    completed_at: '2026-01-01T10:05:00Z',
    structured_output: null,
    raw_output: null,
    tokens_input: 500,
    tokens_output: 200,
    error_message: null,
    retry_count: 0,
  }

  it('should return pipeline status with stages', async () => {
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: [stageRow] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-1' }, ctx)

    expect(result).not.toBeNull()
    expect(result!.taskId).toBe('task-1')
    expect(result!.pipeline).toBe('feature-pipeline')
    expect(result!.currentStage).toBe(1)
    expect(result!.totalStages).toBe(1)
    expect(result!.status).toBe('EXECUTING')
    expect(result!.stages).toHaveLength(1)
  })

  it('should return null when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'nonexistent' }, ctx)
    expect(result).toBeNull()
  })

  it('should return empty stages array when no stages exist', async () => {
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-1' }, ctx)
    expect(result!.totalStages).toBe(0)
    expect(result!.stages).toHaveLength(0)
  })
})

// ── Query.agentInstances ───────────────────────────────────────────────────────

describe('Query.agentInstances', () => {
  it('should map agent_instances rows to camelCase', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            agent_name: 'analyze-agent',
            active_count: 1,
            total_executions: 42,
            total_tokens_used: 100000,
            last_execution_at: '2026-01-01T12:00:00Z',
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.agentInstances(null, null, ctx)

    expect(result).toHaveLength(1)
    expect(result[0].agentName).toBe('analyze-agent')
    expect(result[0].activeCount).toBe(1)
    expect(result[0].totalExecutions).toBe(42)
    expect(result[0].totalTokensUsed).toBe(100000)
    expect(result[0].lastExecutionAt).toBe('2026-01-01T12:00:00Z')
  })

  it('should set lastExecutionAt to null when absent', async () => {
    const pool = mockPool([
      {
        rows: [
          {
            agent_name: 'review-agent',
            active_count: 0,
            total_executions: 0,
            total_tokens_used: 0,
            last_execution_at: null,
          },
        ],
      },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.agentInstances(null, null, ctx)
    expect(result[0].lastExecutionAt).toBeNull()
  })

  it('should return empty array when no agents are registered', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)
    const result = await Query.agentInstances(null, null, ctx)
    expect(result).toHaveLength(0)
  })
})
