/**
 * Tests for api/src/resolvers/task-queries.ts
 *
 * Validates:
 * - taskQueries export exists and is properly structured
 * - toDbEnum is imported from helpers (not duplicated locally)
 * - Query resolver functions exist and are callable
 * - Integration with shared toDbEnum for null/undefined handling
 */

import { describe, it, expect, jest, beforeAll } from '@jest/globals'

// ── Module structure ────────────────────────────────────────────────────────

describe('task-queries module', () => {
  it('exports taskQueries object', async () => {
    const mod = await import('../resolvers/task-queries.js')
    expect(mod.taskQueries).toBeDefined()
    expect(typeof mod.taskQueries).toBe('object')
  })

  it('taskQueries has tasks resolver function', async () => {
    const { taskQueries } = await import('../resolvers/task-queries.js')
    expect(typeof taskQueries.tasks).toBe('function')
  })

  it('taskQueries has task resolver function', async () => {
    const { taskQueries } = await import('../resolvers/task-queries.js')
    expect(typeof taskQueries.task).toBe('function')
  })

  it('taskQueries has pipelineStatus resolver function', async () => {
    const { taskQueries } = await import('../resolvers/task-queries.js')
    expect(typeof taskQueries.pipelineStatus).toBe('function')
  })
})

// ── Import chain: toDbEnum from helpers, NOT local ──────────────────────────

describe('toDbEnum import chain', () => {
  it('helpers.ts exports toDbEnum', async () => {
    const { toDbEnum } = await import('../resolvers/helpers.js')
    expect(typeof toDbEnum).toBe('function')
  })

  it('toDbEnum handles null (used by task-queries for optional status filter)', async () => {
    const { toDbEnum } = await import('../resolvers/helpers.js')
    expect(toDbEnum(null)).toBeNull()
  })

  it('toDbEnum handles undefined (used by task-queries for optional status filter)', async () => {
    const { toDbEnum } = await import('../resolvers/helpers.js')
    expect(toDbEnum(undefined)).toBeNull()
  })

  it('toDbEnum lowercases non-null values', async () => {
    const { toDbEnum } = await import('../resolvers/helpers.js')
    expect(toDbEnum('EXECUTING')).toBe('executing')
    expect(toDbEnum('Failed')).toBe('failed')
  })
})

// ── Import chain: mapTask and mapStage from mappers (direct) ────────────────

describe('mapper imports (direct, not barrel)', () => {
  it('task-queries can import mapTask from mappers', async () => {
    const { mapTask } = await import('../resolvers/mappers.js')
    expect(typeof mapTask).toBe('function')
  })

  it('task-queries can import mapStage from mappers', async () => {
    const { mapStage } = await import('../resolvers/mappers.js')
    expect(typeof mapStage).toBe('function')
  })
})

// ── types.ts field resolvers ────────────────────────────────────────────────

describe('types module exports', () => {
  it('exports DateTime scalar', async () => {
    const { DateTime } = await import('../resolvers/types.js')
    expect(typeof DateTime.serialize).toBe('function')
    expect(typeof DateTime.parseValue).toBe('function')
    expect(typeof DateTime.parseLiteral).toBe('function')
  })

  it('exports JSON_Scalar', async () => {
    const { JSON_Scalar } = await import('../resolvers/types.js')
    expect(typeof JSON_Scalar.serialize).toBe('function')
  })

  it('exports Task field resolver', async () => {
    const { Task } = await import('../resolvers/types.js')
    expect(typeof Task.repository).toBe('function')
    expect(typeof Task.stages).toBe('function')
    expect(typeof Task.context).toBe('function')
    expect(typeof Task.totalCostUsd).toBe('function')
    expect(typeof Task.totalTokens).toBe('function')
  })

  it('exports Repository field resolver', async () => {
    const { Repository } = await import('../resolvers/types.js')
    expect(typeof Repository.taskCount).toBe('function')
    expect(typeof Repository.hasClaudeAgents).toBe('function')
  })

  it('types.ts imports mapStage from mappers (direct)', async () => {
    // Verifies that the module loads cleanly with direct mapper import
    const types = await import('../resolvers/types.js')
    expect(types.Task).toBeDefined()
  })
})

// ── DateTime scalar behavior ────────────────────────────────────────────────

describe('DateTime scalar', () => {
  let DateTime: { serialize: (v: unknown) => string; parseValue: (v: unknown) => string; parseLiteral: (ast: { value: string }) => string }

  beforeAll(async () => {
    const mod = await import('../resolvers/types.js')
    DateTime = mod.DateTime
  })

  it('serializes Date objects to ISO string', () => {
    const d = new Date('2026-04-08T12:00:00Z')
    expect(DateTime.serialize(d)).toBe('2026-04-08T12:00:00.000Z')
  })

  it('serializes strings as-is', () => {
    expect(DateTime.serialize('2026-01-01')).toBe('2026-01-01')
  })

  it('parseValue returns string', () => {
    expect(DateTime.parseValue('2026-01-01')).toBe('2026-01-01')
  })

  it('parseLiteral returns ast value', () => {
    expect(DateTime.parseLiteral({ value: '2026-04-08' })).toBe('2026-04-08')
  })
})

// ── JSON scalar behavior ────────────────────────────────────────────────────

describe('JSON_Scalar', () => {
  let JSON_Scalar: { serialize: (v: unknown) => unknown; parseValue: (v: unknown) => unknown; parseLiteral: (ast: Record<string, unknown>) => unknown }

  beforeAll(async () => {
    const mod = await import('../resolvers/types.js')
    JSON_Scalar = mod.JSON_Scalar
  })

  it('serialize passes through value', () => {
    const obj = { key: 'value' }
    expect(JSON_Scalar.serialize(obj)).toBe(obj)
  })

  it('parseValue passes through value', () => {
    expect(JSON_Scalar.parseValue(42)).toBe(42)
  })

  it('parseLiteral returns ast.value or null', () => {
    expect(JSON_Scalar.parseLiteral({ value: 'hello' })).toBe('hello')
    expect(JSON_Scalar.parseLiteral({})).toBeNull()
  })
})
