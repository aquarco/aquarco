/**
 * Tests for GraphQL mutation resolvers (api/src/resolvers/mutations.ts)
 *
 * All PostgreSQL pool interactions are mocked. Each test verifies:
 *   - Happy path: task/repo payload returned with correct shape
 *   - Error payloads: { task: null, errors: [{ field, message }] }
 *   - SQL construction: correct status values passed (lowercase DB enums)
 *   - Timestamp side effects: status transitions set the right timestamp columns
 */

import { Mutation } from '../resolvers/mutations.js'
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
  }
}

/** A complete DB task row returned from INSERT/UPDATE RETURNING. */
const baseTaskRow: Record<string, unknown> = {
  id: 'task-1',
  title: 'Add feature X',
  category: 'implementation',
  status: 'pending',
  priority: 5,
  source: 'github-issue',
  source_ref: '99',
  pipeline: 'feature-pipeline',
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

const baseRepoRow: Record<string, unknown> = {
  name: 'new-repo',
  url: 'https://github.com/org/new-repo',
  branch: 'main',
  clone_dir: '/repos/new-repo',
  pollers: [],
  last_cloned_at: null,
  last_pulled_at: null,
  clone_status: 'pending',
  head_sha: null,
}

// ── createTask ─────────────────────────────────────────────────────────────────

describe('Mutation.createTask', () => {
  const validInput = {
    title: 'Add feature X',
    category: 'IMPLEMENTATION',
    repository: 'my-repo',
    source: 'github-issue',
    sourceRef: '99',
    pipeline: 'feature-pipeline',
    priority: 5,
    initialContext: null,
  }

  it('should return task payload on success', async () => {
    const pool = mockPool([
      { rows: [{ name: 'my-repo' }] },   // repo check
      { rows: [baseTaskRow] },            // INSERT RETURNING
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.createTask(null, { input: validInput }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.task).not.toBeNull()
    expect(result.task!.id).toBe('task-1')
    expect(result.task!.category).toBe('IMPLEMENTATION')
    expect(result.task!.status).toBe('PENDING')
  })

  it('should lowercase category when inserting', async () => {
    const pool = mockPool([
      { rows: [{ name: 'my-repo' }] },
      { rows: [baseTaskRow] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.createTask(null, { input: validInput }, ctx)

    // Second call is the INSERT — params[1] is the category value
    const insertParams = pool.query.mock.calls[1][1] as unknown[]
    expect(insertParams[1]).toBe('implementation')
  })

  it('should return error payload when repository does not exist', async () => {
    const pool = mockPool([
      { rows: [] }, // repo check: not found
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.createTask(null, { input: validInput }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].field).toBe('repository')
    expect(result.errors[0].message).toContain('my-repo')
  })

  it('should use default priority of 5 when not provided', async () => {
    const pool = mockPool([
      { rows: [{ name: 'my-repo' }] },
      { rows: [baseTaskRow] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.createTask(null, {
      input: { ...validInput, priority: null },
    }, ctx)

    const insertParams = pool.query.mock.calls[1][1] as unknown[]
    // priority is params[6] (0-indexed)
    expect(insertParams[6]).toBe(5)
  })

  it('should stringify initialContext as JSON', async () => {
    const pool = mockPool([
      { rows: [{ name: 'my-repo' }] },
      { rows: [baseTaskRow] },
    ])
    const ctx = makeCtx(pool)
    const context = { issueNumber: 42, labels: ['bug'] }

    await Mutation.createTask(null, {
      input: { ...validInput, initialContext: context },
    }, ctx)

    const insertParams = pool.query.mock.calls[1][1] as unknown[]
    expect(insertParams[7]).toBe(JSON.stringify(context))
  })

  it('should return error payload when INSERT throws', async () => {
    const pool = mockPool([{ rows: [{ name: 'my-repo' }] }])
    pool.query
      .mockResolvedValueOnce({ rows: [{ name: 'my-repo' }] })
      .mockRejectedValueOnce(new Error('connection refused'))
    const ctx = makeCtx(pool)

    const result = await Mutation.createTask(null, { input: validInput }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].message).toContain('connection refused')
  })
})

// ── updateTaskStatus ───────────────────────────────────────────────────────────

describe('Mutation.updateTaskStatus', () => {
  it('should return updated task on success', async () => {
    const updatedRow = { ...baseTaskRow, status: 'executing', started_at: '2026-01-02T00:00:00Z' }
    const pool = mockPool([{ rows: [updatedRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'EXECUTING' }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.task!.status).toBe('EXECUTING')
  })

  it('should lowercase status for DB update', async () => {
    const pool = mockPool([{ rows: [{ ...baseTaskRow, status: 'executing' }] }])
    const ctx = makeCtx(pool)

    await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'EXECUTING' }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('executing')
  })

  it('should include started_at clause when transitioning to executing', async () => {
    const pool = mockPool([{ rows: [{ ...baseTaskRow, status: 'executing' }] }])
    const ctx = makeCtx(pool)

    await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'EXECUTING' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('started_at')
  })

  it('should include completed_at clause when transitioning to completed', async () => {
    const pool = mockPool([{ rows: [{ ...baseTaskRow, status: 'completed' }] }])
    const ctx = makeCtx(pool)

    await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'COMPLETED' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('completed_at')
  })

  it('should include completed_at clause when transitioning to failed', async () => {
    const pool = mockPool([{ rows: [{ ...baseTaskRow, status: 'failed' }] }])
    const ctx = makeCtx(pool)

    await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'FAILED' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('completed_at')
  })

  it('should not include timestamp clause for BLOCKED transition', async () => {
    const pool = mockPool([{ rows: [{ ...baseTaskRow, status: 'blocked' }] }])
    const ctx = makeCtx(pool)

    await Mutation.updateTaskStatus(null, { id: 'task-1', status: 'BLOCKED' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).not.toContain('started_at')
    expect(sql).not.toContain('completed_at')
  })

  it('should return error payload when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.updateTaskStatus(null, { id: 'missing', status: 'COMPLETED' }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].field).toBe('id')
    expect(result.errors[0].message).toContain('missing')
  })
})

// ── retryTask ──────────────────────────────────────────────────────────────────

describe('Mutation.retryTask', () => {
  it('should reset task to pending with incremented retry_count', async () => {
    const retriedRow = { ...baseTaskRow, status: 'pending', retry_count: 1, error_message: null }
    const pool = mockPool([{ rows: [retriedRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.retryTask(null, { id: 'task-1' }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.task!.status).toBe('PENDING')
    expect(result.task!.retryCount).toBe(1)
    expect(result.task!.errorMessage).toBeNull()
  })

  it('should clear started_at and completed_at in the SQL', async () => {
    const pool = mockPool([{ rows: [baseTaskRow] }])
    const ctx = makeCtx(pool)

    await Mutation.retryTask(null, { id: 'task-1' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain("started_at = NULL")
    expect(sql).toContain("completed_at = NULL")
    expect(sql).toContain("error_message = NULL")
  })

  it('should return error payload when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.retryTask(null, { id: 'ghost' }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].field).toBe('id')
  })
})

// ── cancelTask ─────────────────────────────────────────────────────────────────

describe('Mutation.cancelTask', () => {
  it('should set status to failed with cancellation message', async () => {
    const cancelledRow = {
      ...baseTaskRow,
      status: 'failed',
      error_message: 'Task cancelled by user',
    }
    const pool = mockPool([{ rows: [cancelledRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.cancelTask(null, { id: 'task-1' }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.task!.status).toBe('FAILED')
    expect(result.task!.errorMessage).toBe('Task cancelled by user')
  })

  it('should include completed_at in the SQL', async () => {
    const pool = mockPool([{ rows: [baseTaskRow] }])
    const ctx = makeCtx(pool)

    await Mutation.cancelTask(null, { id: 'task-1' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('completed_at = NOW()')
  })

  it('should return error payload when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.cancelTask(null, { id: 'nonexistent' }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].field).toBe('id')
  })
})

// ── registerRepository ─────────────────────────────────────────────────────────

describe('Mutation.registerRepository', () => {
  const validInput = {
    name: 'new-repo',
    url: 'https://github.com/org/new-repo',
    branch: 'main',
    cloneDir: '/repos/new-repo',
    pollers: ['github-issues'],
  }

  it('should return repository payload on success', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.registerRepository(null, { input: validInput }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.repository).not.toBeNull()
    expect(result.repository!.name).toBe('new-repo')
  })

  it('should default branch to main when not provided', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }])
    const ctx = makeCtx(pool)

    await Mutation.registerRepository(null, {
      input: { ...validInput, branch: null },
    }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[2]).toBe('main')
  })

  it('should default pollers to empty array when not provided', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }])
    const ctx = makeCtx(pool)

    await Mutation.registerRepository(null, {
      input: { ...validInput, pollers: null },
    }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[4]).toEqual([])
  })

  it('should return duplicate error when name already exists', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('duplicate key value violates unique constraint'))
    const ctx = makeCtx(pool)

    const result = await Mutation.registerRepository(null, { input: validInput }, ctx)

    expect(result.repository).toBeNull()
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('already exists')
  })

  it('should return generic error payload on other DB errors', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('network timeout'))
    const ctx = makeCtx(pool)

    const result = await Mutation.registerRepository(null, { input: validInput }, ctx)

    expect(result.repository).toBeNull()
    expect(result.errors[0].message).toContain('network timeout')
  })
})

// ── removeRepository ───────────────────────────────────────────────────────────

describe('Mutation.removeRepository', () => {
  it('should return deleted repository on success', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] },      // task check
      { rows: [baseRepoRow] },          // DELETE RETURNING
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.removeRepository(null, { name: 'new-repo' }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.repository!.name).toBe('new-repo')
  })

  it('should return error when repository has associated tasks', async () => {
    const pool = mockPool([
      { rows: [{ count: '3' }] }, // task check: 3 tasks exist
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.removeRepository(null, { name: 'new-repo' }, ctx)

    expect(result.repository).toBeNull()
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('tasks referencing it')
  })

  it('should return error when repository not found', async () => {
    const pool = mockPool([
      { rows: [{ count: '0' }] }, // task check: no tasks
      { rows: [] },               // DELETE: no rows returned
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.removeRepository(null, { name: 'nonexistent' }, ctx)

    expect(result.repository).toBeNull()
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('not found')
  })
})

// ── unblockTask ────────────────────────────────────────────────────────────────

describe('Mutation.unblockTask', () => {
  it('should set task back to pending and insert resolution context', async () => {
    const pool = mockPool([
      { rows: [{ status: 'blocked' }] },     // status check
      { rows: [] },                           // INSERT into context
      { rows: [{ ...baseTaskRow, status: 'pending' }] }, // UPDATE RETURNING
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.unblockTask(null, {
      id: 'task-1',
      resolution: 'Fixed the dependency issue',
    }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.task!.status).toBe('PENDING')
  })

  it('should insert resolution text into context table', async () => {
    const pool = mockPool([
      { rows: [{ status: 'blocked' }] },
      { rows: [] },
      { rows: [{ ...baseTaskRow, status: 'pending' }] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.unblockTask(null, {
      id: 'task-1',
      resolution: 'Dependency resolved',
    }, ctx)

    // Second call is the context INSERT
    const contextSql = pool.query.mock.calls[1][0] as string
    expect(contextSql).toContain('context')
    expect(contextSql).toContain('unblock_resolution')
    const contextParams = pool.query.mock.calls[1][1] as unknown[]
    expect(contextParams[1]).toBe('Dependency resolved')
  })

  it('should return error when task not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.unblockTask(null, {
      id: 'nonexistent',
      resolution: 'Whatever',
    }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].field).toBe('id')
    expect(result.errors[0].message).toContain('not found')
  })

  it('should return error when task is not blocked', async () => {
    const pool = mockPool([{ rows: [{ status: 'pending' }] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.unblockTask(null, {
      id: 'task-1',
      resolution: 'Not actually blocked',
    }, ctx)

    expect(result.task).toBeNull()
    expect(result.errors[0].field).toBe('id')
    expect(result.errors[0].message).toContain('not blocked')
  })
})
