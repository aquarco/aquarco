/**
 * Tests for pipeline execution history changes (GitHub issue #39).
 *
 * Covers:
 *   - mapStage: iteration and run fields are correctly mapped from DB rows
 *   - mapStage: null DB values for iteration/run are defaulted to 1
 *   - Query.pipelineStatus: totalStages uses Set for distinct stage_number values
 *   - Query.pipelineStatus: multiple rows with the same stage_number count as ONE stage
 *   - Query.pipelineStatus: stages array contains ALL rows (not deduplicated)
 *   - Query.pipelineStatus: stages are returned in the correct chronological order
 *   - StageRow interface: iteration and run fields are present and typed correctly
 *
 * NOT covered here (covered by resolvers.test.ts):
 *   - Generic mapStage field mapping and null-coalescing of non-history fields
 *   - Query.pipelineStatus task-not-found and empty-stages cases
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
  }
}

// ── Shared fixture ─────────────────────────────────────────────────────────────

const baseStageRow: Record<string, unknown> = {
  id: 'stage-1',
  task_id: 'task-42',
  stage_number: 0,
  iteration: null,
  run: null,
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
  live_output: null,
}

const taskRow: Record<string, unknown> = {
  id: 'task-42',
  pipeline: 'feature-pipeline',
  current_stage: 1,
  status: 'executing',
}

// ── mapStage: iteration and run fields ────────────────────────────────────────

describe('mapStage — iteration and run fields', () => {
  it('should map explicit iteration value from DB row', () => {
    const result = mapStage({ ...baseStageRow, iteration: 3 })
    expect(result.iteration).toBe(3)
  })

  it('should map explicit run value from DB row', () => {
    const result = mapStage({ ...baseStageRow, run: 2 })
    expect(result.run).toBe(2)
  })

  it('should default iteration to 1 when DB column is null', () => {
    const result = mapStage({ ...baseStageRow, iteration: null })
    expect(result.iteration).toBe(1)
  })

  it('should default run to 1 when DB column is null', () => {
    const result = mapStage({ ...baseStageRow, run: null })
    expect(result.run).toBe(1)
  })

  it('should default iteration to 1 when DB column is undefined', () => {
    const row = { ...baseStageRow }
    delete row.iteration
    const result = mapStage(row)
    expect(result.iteration).toBe(1)
  })

  it('should default run to 1 when DB column is undefined', () => {
    const row = { ...baseStageRow }
    delete row.run
    const result = mapStage(row)
    expect(result.run).toBe(1)
  })

  it('should map iteration=1 and run=1 correctly when DB provides them explicitly', () => {
    const result = mapStage({ ...baseStageRow, iteration: 1, run: 1 })
    expect(result.iteration).toBe(1)
    expect(result.run).toBe(1)
  })

  it('should map high iteration and run values without truncation', () => {
    const result = mapStage({ ...baseStageRow, iteration: 99, run: 42 })
    expect(result.iteration).toBe(99)
    expect(result.run).toBe(42)
  })

  it('should include iteration and run alongside other mapped fields', () => {
    const result = mapStage({ ...baseStageRow, iteration: 2, run: 3 })
    expect(result.id).toBe('stage-1')
    expect(result.taskId).toBe('task-42')
    expect(result.stageNumber).toBe(0)
    expect(result.iteration).toBe(2)
    expect(result.run).toBe(3)
    expect(result.category).toBe('ANALYZE')
    expect(result.status).toBe('COMPLETED')
  })
})

// ── Query.pipelineStatus: totalStages uses Set ────────────────────────────────

describe('Query.pipelineStatus — totalStages with Set-based deduplication', () => {
  it('should count one stage when all rows have the same stage_number', async () => {
    // Three runs of the same stage (stage_number=0) should count as 1 unique stage
    const stageRows = [
      { ...baseStageRow, id: 'stage-1', stage_number: 0, iteration: 1, run: 1 },
      { ...baseStageRow, id: 'stage-2', stage_number: 0, iteration: 1, run: 2 },
      { ...baseStageRow, id: 'stage-3', stage_number: 0, iteration: 2, run: 1 },
    ]
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: stageRows },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    expect(result).not.toBeNull()
    expect(result!.totalStages).toBe(1)
  })

  it('should count distinct stage_numbers regardless of how many runs exist', async () => {
    // stage_number 0: 2 runs, stage_number 1: 1 run, stage_number 2: 3 runs => 3 distinct stages
    const stageRows = [
      { ...baseStageRow, id: 's-0a', stage_number: 0, iteration: 1, run: 1 },
      { ...baseStageRow, id: 's-0b', stage_number: 0, iteration: 1, run: 2 },
      { ...baseStageRow, id: 's-1a', stage_number: 1, iteration: 1, run: 1 },
      { ...baseStageRow, id: 's-2a', stage_number: 2, iteration: 1, run: 1 },
      { ...baseStageRow, id: 's-2b', stage_number: 2, iteration: 2, run: 1 },
      { ...baseStageRow, id: 's-2c', stage_number: 2, iteration: 3, run: 1 },
    ]
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: stageRows },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    expect(result!.totalStages).toBe(3)
  })

  it('should equal stages.length when every stage_number is unique (no repeated runs)', async () => {
    const stageRows = [
      { ...baseStageRow, id: 's-0', stage_number: 0 },
      { ...baseStageRow, id: 's-1', stage_number: 1 },
      { ...baseStageRow, id: 's-2', stage_number: 2 },
      { ...baseStageRow, id: 's-3', stage_number: 3 },
    ]
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: stageRows },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    expect(result!.totalStages).toBe(4)
    expect(result!.stages).toHaveLength(4)
  })

  it('should return zero totalStages when no stages exist', async () => {
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    expect(result!.totalStages).toBe(0)
  })
})

// ── Query.pipelineStatus: stages array is NOT deduplicated ────────────────────

describe('Query.pipelineStatus — stages array contains all runs', () => {
  it('should include all stage rows including repeated stage_number runs', async () => {
    const stageRows = [
      { ...baseStageRow, id: 's-0a', stage_number: 0, iteration: 1, run: 1, status: 'completed' },
      { ...baseStageRow, id: 's-0b', stage_number: 0, iteration: 1, run: 2, status: 'completed' },
      { ...baseStageRow, id: 's-1a', stage_number: 1, iteration: 1, run: 1, status: 'executing' },
    ]
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: stageRows },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    // Three rows returned, but only 2 distinct stage_numbers
    expect(result!.stages).toHaveLength(3)
    expect(result!.totalStages).toBe(2)
  })

  it('should preserve the iteration and run values on each stage in the returned array', async () => {
    const stageRows = [
      { ...baseStageRow, id: 's-run1', stage_number: 0, iteration: 1, run: 1 },
      { ...baseStageRow, id: 's-run2', stage_number: 0, iteration: 1, run: 2 },
    ]
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: stageRows },
    ])
    const ctx = makeCtx(pool)

    const result = await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    const [first, second] = result!.stages
    expect(first.id).toBe('s-run1')
    expect(first.iteration).toBe(1)
    expect(first.run).toBe(1)

    expect(second.id).toBe('s-run2')
    expect(second.iteration).toBe(1)
    expect(second.run).toBe(2)
  })
})

// ── Query.pipelineStatus: SQL query does not use DISTINCT ON ─────────────────

describe('Query.pipelineStatus — SQL query ordering', () => {
  it('should order stages by stage_number, iteration, run in the SQL query', async () => {
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    const stagesQuery = (pool.query.mock.calls[1][0] as string).replace(/\s+/g, ' ').trim()
    expect(stagesQuery).toContain('ORDER BY')
    expect(stagesQuery).toContain('stage_number')
    expect(stagesQuery).toContain('iteration')
    expect(stagesQuery).toContain('run')
  })

  it('should NOT use DISTINCT ON in the stages SQL query', async () => {
    const pool = mockPool([
      { rows: [taskRow] },
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    await Query.pipelineStatus(null, { taskId: 'task-42' }, ctx)

    const stagesQuery = (pool.query.mock.calls[1][0] as string).toUpperCase()
    expect(stagesQuery).not.toContain('DISTINCT ON')
  })
})
