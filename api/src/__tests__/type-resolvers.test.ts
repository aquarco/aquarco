/**
 * Tests for GraphQL type field resolvers (api/src/resolvers/types.ts)
 *
 * Covers:
 *   - Repository.hasClaudeAgents: simplified to check agent_definitions with autoload source only
 *   - Repository.taskCount: counts tasks for a repository
 *   - Task.totalCostUsd: sums stage costs for a task
 *   - mapRepository: new fields (deployPublicKey, errorMessage)
 */

import { jest, describe, it, expect } from '@jest/globals'
import { Repository, Task, DateTime, JSON_Scalar } from '../resolvers/types.js'
import { mapRepository } from '../resolvers/queries.js'
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

// ── Repository.hasClaudeAgents (simplified — no longer checks repo_agent_scans) ─

describe('Repository.hasClaudeAgents', () => {
  it('should return true when autoloaded agent definitions exist for the repo', async () => {
    const pool = mockPool([{ rows: [{ count: '2' }] }])
    const ctx = makeCtx(pool)

    const result = await Repository.hasClaudeAgents({ name: 'my-repo' }, null, ctx)

    expect(result).toBe(true)
  })

  it('should return false when no autoloaded agent definitions exist', async () => {
    const pool = mockPool([{ rows: [{ count: '0' }] }])
    const ctx = makeCtx(pool)

    const result = await Repository.hasClaudeAgents({ name: 'my-repo' }, null, ctx)

    expect(result).toBe(false)
  })

  it('should query agent_definitions with autoload:<repoName> source', async () => {
    const pool = mockPool([{ rows: [{ count: '0' }] }])
    const ctx = makeCtx(pool)

    await Repository.hasClaudeAgents({ name: 'test-repo' }, null, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('agent_definitions')
    expect(sql).toContain('source = $1')
    expect(sql).toContain('is_active = true')
    expect(pool.query.mock.calls[0][1]).toEqual(['autoload:test-repo'])
  })

  it('should NOT query repo_agent_scans (table removed)', async () => {
    const pool = mockPool([{ rows: [{ count: '0' }] }])
    const ctx = makeCtx(pool)

    await Repository.hasClaudeAgents({ name: 'my-repo' }, null, ctx)

    // Only one query should be made (to agent_definitions), not two
    expect(pool.query).toHaveBeenCalledTimes(1)
    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).not.toContain('repo_agent_scans')
  })

  it('should work with RepositoryRow parent type', async () => {
    const pool = mockPool([{ rows: [{ count: '1' }] }])
    const ctx = makeCtx(pool)

    const parent = { name: 'repo-with-agents', url: '', branch: 'main' }
    const result = await Repository.hasClaudeAgents(parent, null, ctx)

    expect(result).toBe(true)
  })
})

// ── Repository.taskCount ─────────────────────────────────────────────────────

describe('Repository.taskCount', () => {
  it('should return the count of tasks for a repository', async () => {
    const pool = mockPool([{ rows: [{ count: '5' }] }])
    const ctx = makeCtx(pool)

    const result = await Repository.taskCount({ name: 'my-repo' }, null, ctx)

    expect(result).toBe(5)
  })

  it('should return 0 when no tasks exist', async () => {
    const pool = mockPool([{ rows: [{ count: '0' }] }])
    const ctx = makeCtx(pool)

    const result = await Repository.taskCount({ name: 'empty-repo' }, null, ctx)

    expect(result).toBe(0)
  })

  it('should query with repository name parameter', async () => {
    const pool = mockPool([{ rows: [{ count: '0' }] }])
    const ctx = makeCtx(pool)

    await Repository.taskCount({ name: 'test-repo' }, null, ctx)

    expect(pool.query).toHaveBeenCalledWith(
      'SELECT COUNT(*) AS count FROM tasks WHERE repository = $1',
      ['test-repo']
    )
  })
})

// ── Task.totalCostUsd ────────────────────────────────────────────────────────

describe('Task.totalCostUsd', () => {
  it('should return total cost when > 0', async () => {
    const pool = mockPool([{ rows: [{ total: '1.25' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalCostUsd({ id: 'task-1' }, null, ctx)

    expect(result).toBe(1.25)
  })

  it('should return null when cost is 0', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    const result = await Task.totalCostUsd({ id: 'task-1' }, null, ctx)

    expect(result).toBeNull()
  })

  it('should query stages by task id', async () => {
    const pool = mockPool([{ rows: [{ total: '0' }] }])
    const ctx = makeCtx(pool)

    await Task.totalCostUsd({ id: 'task-42' }, null, ctx)

    expect(pool.query).toHaveBeenCalledWith(
      'SELECT COALESCE(SUM(cost_usd), 0) AS total FROM stages WHERE task_id = $1',
      ['task-42']
    )
  })
})

// ── mapRepository: new fields ────────────────────────────────────────────────

describe('mapRepository new fields', () => {
  const baseRow: Record<string, unknown> = {
    name: 'my-repo',
    url: 'https://github.com/org/my-repo',
    branch: 'main',
    clone_dir: '/repos/my-repo',
    pollers: [],
    last_cloned_at: null,
    last_pulled_at: null,
    clone_status: 'ready',
    head_sha: null,
  }

  it('should map deployPublicKey from deploy_public_key', () => {
    const result = mapRepository({ ...baseRow, deploy_public_key: 'ssh-ed25519 AAAA...' })
    expect(result.deployPublicKey).toBe('ssh-ed25519 AAAA...')
  })

  it('should default deployPublicKey to null when missing', () => {
    const result = mapRepository(baseRow)
    expect(result.deployPublicKey).toBeNull()
  })

  it('should map errorMessage from error_message', () => {
    const result = mapRepository({ ...baseRow, error_message: 'clone failed' })
    expect(result.errorMessage).toBe('clone failed')
  })

  it('should default errorMessage to null when missing', () => {
    const result = mapRepository(baseRow)
    expect(result.errorMessage).toBeNull()
  })
})

// ── DateTime scalar ──────────────────────────────────────────────────────────

describe('DateTime scalar', () => {
  it('should serialize Date objects to ISO string', () => {
    const date = new Date('2026-01-01T00:00:00Z')
    expect(DateTime.serialize(date)).toBe('2026-01-01T00:00:00.000Z')
  })

  it('should serialize string values as-is', () => {
    expect(DateTime.serialize('2026-01-01')).toBe('2026-01-01')
  })

  it('should parseValue to string', () => {
    expect(DateTime.parseValue('2026-01-01')).toBe('2026-01-01')
  })

  it('should parseLiteral from AST value', () => {
    expect(DateTime.parseLiteral({ value: '2026-01-01' })).toBe('2026-01-01')
  })
})

// ── JSON scalar ──────────────────────────────────────────────────────────────

describe('JSON_Scalar', () => {
  it('should serialize values as-is', () => {
    const obj = { key: 'value' }
    expect(JSON_Scalar.serialize(obj)).toBe(obj)
  })

  it('should parseValue as-is', () => {
    const obj = { key: 'value' }
    expect(JSON_Scalar.parseValue(obj)).toBe(obj)
  })

  it('should parseLiteral returning ast.value or null', () => {
    expect(JSON_Scalar.parseLiteral({ value: 42 })).toBe(42)
    expect(JSON_Scalar.parseLiteral({})).toBeNull()
  })
})
