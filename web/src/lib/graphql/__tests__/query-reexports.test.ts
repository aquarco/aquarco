/**
 * Tests for web/src/lib/graphql/queries.ts barrel re-exports.
 *
 * After codebase simplification (#109), GraphQL queries were split into:
 * - task-queries.ts (task-related queries)
 * - repo-queries.ts (repository-related queries)
 * - agent-queries.ts (agent-related queries)
 * - queries.ts (barrel re-exporter + cross-domain queries)
 *
 * These tests verify that all consumer-facing exports remain available
 * from the barrel file, and that the split files export correctly.
 */

import { describe, it, expect } from 'vitest'

// ── Barrel re-exports ──────────────────────────────────────────────────────

describe('queries.ts barrel re-exports', () => {
  it('re-exports task queries', async () => {
    const mod = await import('../queries.js')
    expect(mod.GET_TASKS).toBeDefined()
    expect(mod.GET_TASK).toBeDefined()
  })

  it('re-exports repo queries', async () => {
    const mod = await import('../queries.js')
    expect(mod.GET_REPOSITORIES).toBeDefined()
  })

  it('re-exports agent queries', async () => {
    const mod = await import('../queries.js')
    expect(mod.GET_AGENT_INSTANCES).toBeDefined()
    expect(mod.GET_GLOBAL_AGENTS).toBeDefined()
  })

  it('keeps cross-domain queries inline', async () => {
    const mod = await import('../queries.js')
    expect(mod.DASHBOARD_STATS).toBeDefined()
    expect(mod.TOKEN_USAGE_BY_MODEL).toBeDefined()
  })
})

// ── Individual query file exports ──────────────────────────────────────────

describe('task-queries.ts', () => {
  it('exports GET_TASKS', async () => {
    const { GET_TASKS } = await import('../task-queries.js')
    expect(GET_TASKS).toBeDefined()
    // Should be a gql DocumentNode (has kind and definitions)
    expect(GET_TASKS.kind).toBe('Document')
    expect(GET_TASKS.definitions.length).toBeGreaterThan(0)
  })

  it('exports GET_TASK', async () => {
    const { GET_TASK } = await import('../task-queries.js')
    expect(GET_TASK).toBeDefined()
    expect(GET_TASK.kind).toBe('Document')
  })
})

describe('repo-queries.ts', () => {
  it('exports GET_REPOSITORIES', async () => {
    const { GET_REPOSITORIES } = await import('../repo-queries.js')
    expect(GET_REPOSITORIES).toBeDefined()
    expect(GET_REPOSITORIES.kind).toBe('Document')
  })
})

describe('agent-queries.ts', () => {
  it('exports GET_AGENT_INSTANCES', async () => {
    const { GET_AGENT_INSTANCES } = await import('../agent-queries.js')
    expect(GET_AGENT_INSTANCES).toBeDefined()
    expect(GET_AGENT_INSTANCES.kind).toBe('Document')
  })

  it('exports GET_GLOBAL_AGENTS', async () => {
    const { GET_GLOBAL_AGENTS } = await import('../agent-queries.js')
    expect(GET_GLOBAL_AGENTS).toBeDefined()
    expect(GET_GLOBAL_AGENTS.kind).toBe('Document')
  })

  it('exports GET_PIPELINE_DEFINITIONS', async () => {
    const { GET_PIPELINE_DEFINITIONS } = await import('../agent-queries.js')
    expect(GET_PIPELINE_DEFINITIONS).toBeDefined()
    expect(GET_PIPELINE_DEFINITIONS.kind).toBe('Document')
  })
})

// ── Cross-domain queries remain in barrel ──────────────────────────────────

describe('DASHBOARD_STATS (cross-domain, inline in barrel)', () => {
  it('is a valid gql DocumentNode', async () => {
    const { DASHBOARD_STATS } = await import('../queries.js')
    expect(DASHBOARD_STATS.kind).toBe('Document')
    expect(DASHBOARD_STATS.definitions[0].kind).toBe('OperationDefinition')
  })
})

describe('TOKEN_USAGE_BY_MODEL (cross-domain, inline in barrel)', () => {
  it('is a valid gql DocumentNode', async () => {
    const { TOKEN_USAGE_BY_MODEL } = await import('../queries.js')
    expect(TOKEN_USAGE_BY_MODEL.kind).toBe('Document')
    expect(TOKEN_USAGE_BY_MODEL.definitions[0].kind).toBe('OperationDefinition')
  })
})
