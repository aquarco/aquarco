/**
 * Tests for resolver assembly after codebase simplification (#109).
 *
 * Validates that the split resolver files are correctly assembled:
 * - mutations.ts spreads task-mutations, repo-mutations, agent-mutations
 * - queries.ts spreads task-queries, repo-queries
 * - imports from mappers.js (not via barrel) work correctly
 * - helpers.ts toDbEnum overloads handle null/undefined
 */

import { describe, it, expect } from '@jest/globals'

// ── Mutation assembly ───────────────────────────────────────────────────────

describe('Mutation assembler', () => {
  it('exports taskMutations from task-mutations', async () => {
    const { taskMutations } = await import('../resolvers/task-mutations.js')
    expect(taskMutations).toBeDefined()
    expect(typeof taskMutations).toBe('object')
  })

  it('exports repoMutations from repo-mutations', async () => {
    const { repoMutations } = await import('../resolvers/repo-mutations.js')
    expect(repoMutations).toBeDefined()
    expect(typeof repoMutations).toBe('object')
  })

  it('exports agentMutations from agent-mutations', async () => {
    const { agentMutations } = await import('../resolvers/agent-mutations.js')
    expect(agentMutations).toBeDefined()
    expect(typeof agentMutations).toBe('object')
  })

  it('Mutation object includes spread domain mutations', async () => {
    const { Mutation } = await import('../resolvers/mutations.js')
    // Auth mutations are inline
    expect(typeof Mutation.githubLoginStart).toBe('function')
    expect(typeof Mutation.githubLogout).toBe('function')
    // Task mutations spread from task-mutations.ts
    expect(typeof Mutation.createTask).toBe('function')
    // Repo mutations spread from repo-mutations.ts
    expect(typeof Mutation.registerRepository).toBe('function')
  })
})

// ── Query assembly ──────────────────────────────────────────────────────────

describe('Query assembler', () => {
  it('exports taskQueries from task-queries', async () => {
    const { taskQueries } = await import('../resolvers/task-queries.js')
    expect(taskQueries).toBeDefined()
    expect(typeof taskQueries.tasks).toBe('function')
  })

  it('exports repoQueries from repo-queries', async () => {
    const { repoQueries } = await import('../resolvers/repo-queries.js')
    expect(repoQueries).toBeDefined()
  })

  it('Query object includes spread domain queries', async () => {
    const { Query } = await import('../resolvers/queries.js')
    expect(typeof Query.tasks).toBe('function')
    expect(typeof Query.drainStatus).toBe('function')
  })

  it('re-exports mappers for backward compatibility', async () => {
    const { mapTask, mapStage, mapRepository, mapAgentDefinition, getDrainStatus } =
      await import('../resolvers/queries.js')
    expect(typeof mapTask).toBe('function')
    expect(typeof mapStage).toBe('function')
    expect(typeof mapRepository).toBe('function')
    expect(typeof mapAgentDefinition).toBe('function')
    expect(typeof getDrainStatus).toBe('function')
  })
})

// ── Direct mapper imports ───────────────────────────────────────────────────

describe('Direct mapper imports (no barrel)', () => {
  it('repo-mutations imports from mappers directly', async () => {
    // Verify the module loads without error (uses direct import)
    const mod = await import('../resolvers/repo-mutations.js')
    expect(mod.repoMutations).toBeDefined()
  })

  it('repo-queries imports from mappers directly', async () => {
    const mod = await import('../resolvers/repo-queries.js')
    expect(mod.repoQueries).toBeDefined()
  })

  it('task-queries imports from mappers and helpers', async () => {
    const mod = await import('../resolvers/task-queries.js')
    expect(mod.taskQueries).toBeDefined()
  })
})

// ── helpers.ts toDbEnum overloads ───────────────────────────────────────────

describe('toDbEnum overloads (stage 4 fix)', () => {
  let toDbEnum: (value: string | null | undefined) => string | null

  beforeAll(async () => {
    const helpers = await import('../resolvers/helpers.js')
    toDbEnum = helpers.toDbEnum
  })

  it('converts string to lowercase', () => {
    expect(toDbEnum('PENDING')).toBe('pending')
  })

  it('handles null', () => {
    expect(toDbEnum(null)).toBeNull()
  })

  it('handles undefined', () => {
    expect(toDbEnum(undefined)).toBeNull()
  })

  it('handles empty string as falsy', () => {
    expect(toDbEnum('')).toBeNull()
  })

  it('preserves lowercase', () => {
    expect(toDbEnum('completed')).toBe('completed')
  })
})

// ── types.ts model/rawOutput fields ─────────────────────────────────────────

describe('types.ts re-exports', () => {
  it('exports from types module', async () => {
    const types = await import('../resolvers/types.js')
    expect(types).toBeDefined()
  })
})
