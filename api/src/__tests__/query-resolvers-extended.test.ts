/**
 * Extended tests for GraphQL query resolvers (queries.ts) and type resolvers (types.ts).
 *
 * Covers previously untested code paths:
 *   - fetchAgentWithOverrides: queries agent_definitions with joins, returns mapped row or null
 *   - getDrainStatus / Query.drainStatus: reads supervisor_state + agent_instances + tasks
 *   - Task.repository: delegates to repositoryLoader
 *   - Task.stages: delegates to stagesByTaskLoader and maps via mapStage
 *   - Task.context: delegates to contextByTaskLoader and maps fields
 *   - Task.totalCostUsd edge case: behaviour when result.rows has a row (COALESCE guarantees this)
 *   - mapRepository: cloneStatus uppercase, pollers default, all field mapping
 */

import { jest, describe, it, expect } from '@jest/globals'
import { fetchAgentWithOverrides, mapRepository, mapStage, getDrainStatus } from '../resolvers/queries.js'
import { Task, Repository } from '../resolvers/types.js'
import type { Context } from '../context.js'
import type { StageRow, ContextRow } from '../loaders.js'

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

function makeCtx(
  pool: { query: jest.Mock },
  loaderOverrides: Partial<Context['loaders']> = {}
): Context {
  return {
    pool: pool as unknown as Context['pool'],
    loaders: {
      repositoryLoader: { load: jest.fn() } as unknown as Context['loaders']['repositoryLoader'],
      stagesByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['stagesByTaskLoader'],
      contextByTaskLoader: { load: jest.fn() } as unknown as Context['loaders']['contextByTaskLoader'],
      ...loaderOverrides,
    },
    req: { headers: {} } as unknown as Context['req'],
  }
}

// ── fetchAgentWithOverrides ─────────────────────────────────────────────────

describe('fetchAgentWithOverrides', () => {
  it('should return the first row when agent exists', async () => {
    const agentRow = {
      name: 'test-agent',
      version: '1.0.0',
      description: 'Test',
      spec: { timeout: 300 },
      source: 'default',
      agent_group: 'pipeline',
      is_disabled: false,
      modified_spec: null,
      active_count: 0,
      total_executions: 0,
      total_tokens_used: 0,
      last_execution_at: null,
    }
    const pool = mockPool([{ rows: [agentRow] }])

    const result = await fetchAgentWithOverrides(pool as any, 'test-agent', 'global')

    expect(result).toEqual(agentRow)
    expect(pool.query).toHaveBeenCalledTimes(1)
    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('agent_definitions')
    expect(sql).toContain('agent_overrides')
    expect(sql).toContain('agent_instances')
    expect(pool.query.mock.calls[0][1]).toEqual(['test-agent', 'global'])
  })

  it('should return null when no agent matches', async () => {
    const pool = mockPool([{ rows: [] }])

    const result = await fetchAgentWithOverrides(pool as any, 'nonexistent', 'global')

    expect(result).toBeNull()
  })

  it('should pass scope parameter for repo-level overrides', async () => {
    const pool = mockPool([{ rows: [] }])

    await fetchAgentWithOverrides(pool as any, 'my-agent', 'repo:my-app')

    expect(pool.query.mock.calls[0][1]).toEqual(['my-agent', 'repo:my-app'])
  })
})

// ── getDrainStatus ──────────────────────────────────────────────────────────

describe('getDrainStatus', () => {
  it('should return enabled=true when drain_mode is "true"', async () => {
    const pool = mockPool([{
      rows: [{ drain_val: 'true', active_agents: 3, active_tasks: 2 }],
    }])

    const result = await getDrainStatus(pool as any)

    expect(result).toEqual({
      enabled: true,
      activeAgents: 3,
      activeTasks: 2,
    })
  })

  it('should return enabled=false when drain_mode is not "true"', async () => {
    const pool = mockPool([{
      rows: [{ drain_val: 'false', active_agents: 0, active_tasks: 0 }],
    }])

    const result = await getDrainStatus(pool as any)

    expect(result.enabled).toBe(false)
  })

  it('should return enabled=false when drain_val is null (no supervisor_state row)', async () => {
    const pool = mockPool([{
      rows: [{ drain_val: null, active_agents: 0, active_tasks: 0 }],
    }])

    const result = await getDrainStatus(pool as any)

    expect(result.enabled).toBe(false)
  })

  it('should default activeAgents and activeTasks to 0 when row values are null', async () => {
    const pool = mockPool([{
      rows: [{ drain_val: 'true', active_agents: undefined, active_tasks: undefined }],
    }])

    const result = await getDrainStatus(pool as any)

    expect(result.activeAgents).toBe(0)
    expect(result.activeTasks).toBe(0)
  })

  it('should handle empty rows gracefully', async () => {
    const pool = mockPool([{ rows: [] }])

    const result = await getDrainStatus(pool as any)

    expect(result.enabled).toBe(false)
    expect(result.activeAgents).toBe(0)
    expect(result.activeTasks).toBe(0)
  })
})

// ── Task.repository ─────────────────────────────────────────────────────────

describe('Task.repository', () => {
  it('should delegate to repositoryLoader with _repositoryName', async () => {
    const mockRepoRow = { name: 'my-repo', url: 'https://...', branch: 'main' }
    const loadFn = jest.fn(() => Promise.resolve(mockRepoRow))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      repositoryLoader: { load: loadFn } as any,
    })

    const result = await Task.repository({ _repositoryName: 'my-repo' }, null, ctx)

    expect(loadFn).toHaveBeenCalledWith('my-repo')
    expect(result).toEqual(mockRepoRow)
  })

  it('should return null when loader returns null', async () => {
    const loadFn = jest.fn(() => Promise.resolve(null))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      repositoryLoader: { load: loadFn } as any,
    })

    const result = await Task.repository({ _repositoryName: 'gone-repo' }, null, ctx)

    expect(result).toBeNull()
  })
})

// ── Task.stages ─────────────────────────────────────────────────────────────

describe('Task.stages', () => {
  it('should load stages via stagesByTaskLoader and map them', async () => {
    const rawStages: StageRow[] = [
      {
        id: 's1',
        task_id: 't1',
        stage_number: 0,
        iteration: 1,
        run: 1,
        category: 'review',
        agent: 'review-agent',
        agent_version: '1.0.0',
        status: 'completed',
        started_at: '2026-01-01T00:00:00Z',
        completed_at: '2026-01-01T00:01:00Z',
        structured_output: { summary: 'ok' },
        raw_output: 'done',
        tokens_input: 100,
        tokens_output: 50,
        cost_usd: 0.01,
        cache_read_tokens: 10,
        cache_write_tokens: 5,
        error_message: null,
        retry_count: 0,
        live_output: null,
      },
    ]
    const loadFn = jest.fn(() => Promise.resolve(rawStages))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      stagesByTaskLoader: { load: loadFn } as any,
    })

    const result = await Task.stages({ id: 't1' }, null, ctx)

    expect(loadFn).toHaveBeenCalledWith('t1')
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('s1')
    expect(result[0].category).toBe('REVIEW')
    expect(result[0].status).toBe('COMPLETED')
    expect(result[0].iteration).toBe(1)
    expect(result[0].run).toBe(1)
  })

  it('should return empty array when no stages exist', async () => {
    const loadFn = jest.fn(() => Promise.resolve([]))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      stagesByTaskLoader: { load: loadFn } as any,
    })

    const result = await Task.stages({ id: 't-empty' }, null, ctx)

    expect(result).toHaveLength(0)
  })
})

// ── Task.context ────────────────────────────────────────────────────────────

describe('Task.context', () => {
  it('should load context entries and map snake_case to camelCase', async () => {
    const rawContextRows: ContextRow[] = [
      {
        id: 'c1',
        task_id: 't1',
        stage_number: 0,
        key: 'review_output',
        value_type: 'json',
        value_json: { summary: 'ok' },
        value_text: null,
        value_file_ref: null,
        created_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'c2',
        task_id: 't1',
        stage_number: null,
        key: 'initial_context',
        value_type: 'text',
        value_json: null,
        value_text: 'hello',
        value_file_ref: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    ]
    const loadFn = jest.fn(() => Promise.resolve(rawContextRows))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      contextByTaskLoader: { load: loadFn } as any,
    })

    const result = await Task.context({ id: 't1' }, null, ctx)

    expect(loadFn).toHaveBeenCalledWith('t1')
    expect(result).toHaveLength(2)

    // First entry: has stageNumber and json value
    expect(result[0].id).toBe('c1')
    expect(result[0].taskId).toBe('t1')
    expect(result[0].stageNumber).toBe(0)
    expect(result[0].key).toBe('review_output')
    expect(result[0].valueType).toBe('json')
    expect(result[0].valueJson).toEqual({ summary: 'ok' })
    expect(result[0].valueText).toBeNull()
    expect(result[0].valueFileRef).toBeNull()

    // Second entry: null stageNumber, text value
    expect(result[1].stageNumber).toBeNull()
    expect(result[1].valueText).toBe('hello')
  })

  it('should return empty array when no context entries exist', async () => {
    const loadFn = jest.fn(() => Promise.resolve([]))
    const pool = mockPool([])
    const ctx = makeCtx(pool, {
      contextByTaskLoader: { load: loadFn } as any,
    })

    const result = await Task.context({ id: 't-empty' }, null, ctx)

    expect(result).toHaveLength(0)
  })
})

// ── Task.totalTokens ────────────────────────────────────────────────────────

describe('Task.totalTokens', () => {
  it('should return parsed integer when sum > 0', async () => {
    const pool = mockPool([{ rows: [{ total: '12345' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalTokens({ id: 'task-1' }, null, ctx)

    expect(result).toBe(12345)
  })

  it('should return null when sum is 0', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalTokens({ id: 'task-1' }, null, ctx)

    expect(result).toBeNull()
  })
})

// ── mapRepository: cloneStatus uppercase + pollers default ──────────────────

describe('mapRepository edge cases', () => {
  const baseRow: Record<string, unknown> = {
    name: 'test-repo',
    url: 'https://github.com/org/test',
    branch: 'main',
    clone_dir: '/repos/test',
    pollers: ['github-issues'],
    last_cloned_at: null,
    last_pulled_at: null,
    clone_status: 'ready',
    head_sha: 'abc123',
  }

  it('should uppercase cloneStatus from snake_case DB value', () => {
    const result = mapRepository({ ...baseRow, clone_status: 'cloning' })
    expect(result.cloneStatus).toBe('CLONING')
  })

  it('should handle various clone_status values', () => {
    expect(mapRepository({ ...baseRow, clone_status: 'ready' }).cloneStatus).toBe('READY')
    expect(mapRepository({ ...baseRow, clone_status: 'error' }).cloneStatus).toBe('ERROR')
    expect(mapRepository({ ...baseRow, clone_status: 'pending' }).cloneStatus).toBe('PENDING')
  })

  it('should default pollers to empty array when null/undefined', () => {
    const result = mapRepository({ ...baseRow, pollers: undefined })
    expect(result.pollers).toEqual([])
  })

  it('should map headSha from head_sha', () => {
    const result = mapRepository(baseRow)
    expect(result.headSha).toBe('abc123')
  })

  it('should default headSha to null when missing', () => {
    const result = mapRepository({ ...baseRow, head_sha: null })
    expect(result.headSha).toBeNull()
  })

  it('should set _name for internal use', () => {
    const result = mapRepository(baseRow)
    expect(result._name).toBe('test-repo')
  })

  it('should map cloneDir from clone_dir', () => {
    const result = mapRepository(baseRow)
    expect(result.cloneDir).toBe('/repos/test')
  })

  it('should map lastClonedAt and lastPulledAt with null defaults', () => {
    const result = mapRepository({
      ...baseRow,
      last_cloned_at: '2026-01-01T00:00:00Z',
      last_pulled_at: '2026-02-01T00:00:00Z',
    })
    expect(result.lastClonedAt).toBe('2026-01-01T00:00:00Z')
    expect(result.lastPulledAt).toBe('2026-02-01T00:00:00Z')
  })
})

// ── mapStage: edge cases for null coalescing ────────────────────────────────

describe('mapStage null coalescing', () => {
  const baseRow: Record<string, unknown> = {
    id: 's1',
    task_id: 't1',
    stage_number: 0,
    iteration: 1,
    run: 1,
    category: 'test',
    agent: 'test-agent',
    agent_version: '1.0.0',
    status: 'completed',
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T00:01:00Z',
    structured_output: { result: 'ok' },
    raw_output: 'output text',
    tokens_input: 100,
    tokens_output: 50,
    cost_usd: 0.01,
    cache_read_tokens: 10,
    cache_write_tokens: 5,
    error_message: null,
    retry_count: 0,
    live_output: null,
  }

  it('should null-coalesce optional fields to null', () => {
    const row = {
      ...baseRow,
      agent: undefined,
      agent_version: undefined,
      started_at: undefined,
      completed_at: undefined,
      structured_output: undefined,
      raw_output: undefined,
      tokens_input: undefined,
      tokens_output: undefined,
      cost_usd: undefined,
      cache_read_tokens: undefined,
      cache_write_tokens: undefined,
      error_message: undefined,
      live_output: undefined,
    }
    const result = mapStage(row)

    expect(result.agent).toBeNull()
    expect(result.agentVersion).toBeNull()
    expect(result.startedAt).toBeNull()
    expect(result.completedAt).toBeNull()
    expect(result.structuredOutput).toBeNull()
    expect(result.rawOutput).toBeNull()
    expect(result.tokensInput).toBeNull()
    expect(result.tokensOutput).toBeNull()
    expect(result.costUsd).toBeNull()
    expect(result.cacheReadTokens).toBeNull()
    expect(result.cacheWriteTokens).toBeNull()
    expect(result.errorMessage).toBeNull()
    expect(result.liveOutput).toBeNull()
  })

  it('should uppercase category and status', () => {
    const result = mapStage({ ...baseRow, category: 'implement', status: 'executing' })
    expect(result.category).toBe('IMPLEMENT')
    expect(result.status).toBe('EXECUTING')
  })
})
