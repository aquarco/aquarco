/**
 * Tests for agent-related GraphQL query resolvers and mapAgentDefinition helper.
 *
 * Covers acceptance criteria:
 *   - mapAgentDefinition maps source column to AgentSource enum and extracts sourceRepo
 *   - globalAgents query returns default + global agents with override/metrics state
 *   - repoAgentGroups query groups repo agents by repository name
 */

import { jest, describe, it, expect } from '@jest/globals'
import { Query, mapAgentDefinition } from '../resolvers/queries.js'
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

// ── mapAgentDefinition ─────────────────────────────────────────────────────────

describe('mapAgentDefinition', () => {
  const baseRow: Record<string, unknown> = {
    name: 'analyze-agent',
    version: '1.0.0',
    description: 'Analyzes code changes',
    spec: { timeout: 300 },
    source: 'default',
    is_disabled: false,
    modified_spec: null,
    active_count: 2,
    total_executions: 42,
    total_tokens_used: 100000,
    last_execution_at: '2026-01-15T10:00:00Z',
  }

  it('should map default source to DEFAULT enum with null sourceRepo', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepo).toBeNull()
  })

  it('should map global:<repo> source to GLOBAL_CONFIG with sourceRepo', () => {
    const result = mapAgentDefinition({ ...baseRow, source: 'global:my-config-repo' })
    expect(result.source).toBe('GLOBAL_CONFIG')
    expect(result.sourceRepo).toBe('my-config-repo')
  })

  it('should map repo:<repo> source to REPOSITORY with sourceRepo', () => {
    const result = mapAgentDefinition({ ...baseRow, source: 'repo:my-app' })
    expect(result.source).toBe('REPOSITORY')
    expect(result.sourceRepo).toBe('my-app')
  })

  it('should fall back to DEFAULT when source is unrecognized', () => {
    const result = mapAgentDefinition({ ...baseRow, source: 'unknown-source' })
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepo).toBeNull()
  })

  it('should fall back to DEFAULT when source is null/undefined', () => {
    const result = mapAgentDefinition({ ...baseRow, source: undefined })
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepo).toBeNull()
  })

  it('should map basic fields correctly', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.name).toBe('analyze-agent')
    expect(result.version).toBe('1.0.0')
    expect(result.description).toBe('Analyzes code changes')
  })

  it('should use modified_spec as spec when present', () => {
    const modSpec = { timeout: 600, maxTurns: 100 }
    const result = mapAgentDefinition({ ...baseRow, modified_spec: modSpec })
    expect(result.spec).toEqual(modSpec)
  })

  it('should use base spec when modified_spec is null', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.spec).toEqual({ timeout: 300 })
  })

  it('should set isDisabled to true when is_disabled is true', () => {
    const result = mapAgentDefinition({ ...baseRow, is_disabled: true })
    expect(result.isDisabled).toBe(true)
  })

  it('should set isDisabled to false when is_disabled is false', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.isDisabled).toBe(false)
  })

  it('should set isModified to true when modified_spec is present', () => {
    const result = mapAgentDefinition({ ...baseRow, modified_spec: { timeout: 600 } })
    expect(result.isModified).toBe(true)
  })

  it('should set isModified to false when modified_spec is null', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.isModified).toBe(false)
  })

  it('should return modifiedSpec as-is when present', () => {
    const modSpec = { timeout: 600 }
    const result = mapAgentDefinition({ ...baseRow, modified_spec: modSpec })
    expect(result.modifiedSpec).toEqual(modSpec)
  })

  it('should return null modifiedSpec when absent', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.modifiedSpec).toBeNull()
  })

  it('should parse numeric metrics from string values', () => {
    const result = mapAgentDefinition({
      ...baseRow,
      active_count: '3',
      total_executions: '99',
      total_tokens_used: '50000',
    })
    expect(result.activeCount).toBe(3)
    expect(result.totalExecutions).toBe(99)
    expect(result.totalTokensUsed).toBe(50000)
  })

  it('should default numeric metrics to 0 when null/undefined', () => {
    const result = mapAgentDefinition({
      ...baseRow,
      active_count: null,
      total_executions: undefined,
      total_tokens_used: null,
    })
    expect(result.activeCount).toBe(0)
    expect(result.totalExecutions).toBe(0)
    expect(result.totalTokensUsed).toBe(0)
  })

  it('should set lastExecutionAt from row value', () => {
    const result = mapAgentDefinition(baseRow)
    expect(result.lastExecutionAt).toBe('2026-01-15T10:00:00Z')
  })

  it('should set lastExecutionAt to null when absent', () => {
    const result = mapAgentDefinition({ ...baseRow, last_execution_at: null })
    expect(result.lastExecutionAt).toBeNull()
  })
})

// ── Query.globalAgents ─────────────────────────────────────────────────────────

describe('Query.globalAgents', () => {
  const defaultAgentRow: Record<string, unknown> = {
    name: 'analyze-agent',
    version: '1.0.0',
    description: 'Analyzes code',
    spec: { timeout: 300 },
    source: 'default',
    is_disabled: false,
    modified_spec: null,
    active_count: 1,
    total_executions: 10,
    total_tokens_used: 5000,
    last_execution_at: '2026-01-15T10:00:00Z',
  }

  const globalConfigAgentRow: Record<string, unknown> = {
    name: 'custom-agent',
    version: '2.0.0',
    description: 'Custom agent from config repo',
    spec: { timeout: 600 },
    source: 'global:config-repo',
    is_disabled: true,
    modified_spec: { timeout: 900 },
    active_count: 0,
    total_executions: 5,
    total_tokens_used: 2000,
    last_execution_at: null,
  }

  it('should return agents mapped via mapAgentDefinition', async () => {
    const pool = mockPool([{ rows: [defaultAgentRow, globalConfigAgentRow] }])
    const ctx = makeCtx(pool)

    const result = await Query.globalAgents(null, null, ctx)

    expect(result).toHaveLength(2)
    expect(result[0].name).toBe('analyze-agent')
    expect(result[0].source).toBe('DEFAULT')
    expect(result[1].name).toBe('custom-agent')
    expect(result[1].source).toBe('GLOBAL_CONFIG')
  })

  it('should return empty array when no global agents exist', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.globalAgents(null, null, ctx)
    expect(result).toHaveLength(0)
  })

  it('should query with correct SQL filtering for default and global sources', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.globalAgents(null, null, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain("ad.source = 'default'")
    expect(sql).toContain("ad.source LIKE 'global:%'")
    expect(sql).toContain('ad.is_active = true')
  })

  it('should include override state (isDisabled, isModified) from join', async () => {
    const pool = mockPool([{ rows: [globalConfigAgentRow] }])
    const ctx = makeCtx(pool)

    const result = await Query.globalAgents(null, null, ctx)

    expect(result[0].isDisabled).toBe(true)
    expect(result[0].isModified).toBe(true)
    expect(result[0].modifiedSpec).toEqual({ timeout: 900 })
  })

  it('should include runtime metrics from agent_instances join', async () => {
    const pool = mockPool([{ rows: [defaultAgentRow] }])
    const ctx = makeCtx(pool)

    const result = await Query.globalAgents(null, null, ctx)

    expect(result[0].activeCount).toBe(1)
    expect(result[0].totalExecutions).toBe(10)
    expect(result[0].totalTokensUsed).toBe(5000)
    expect(result[0].lastExecutionAt).toBe('2026-01-15T10:00:00Z')
  })
})

// ── Query.repoAgentGroups ──────────────────────────────────────────────────────

describe('Query.repoAgentGroups', () => {
  const repoAgentRows: Record<string, unknown>[] = [
    {
      name: 'agent-a',
      version: '1.0.0',
      description: 'Agent A for app1',
      spec: { timeout: 300 },
      source: 'repo:app1',
      is_disabled: false,
      modified_spec: null,
      active_count: 0,
      total_executions: 0,
      total_tokens_used: 0,
      last_execution_at: null,
    },
    {
      name: 'agent-b',
      version: '1.0.0',
      description: 'Agent B for app1',
      spec: { timeout: 300 },
      source: 'repo:app1',
      is_disabled: false,
      modified_spec: null,
      active_count: 1,
      total_executions: 5,
      total_tokens_used: 1000,
      last_execution_at: '2026-01-20T10:00:00Z',
    },
    {
      name: 'agent-c',
      version: '1.0.0',
      description: 'Agent C for app2',
      spec: { timeout: 600 },
      source: 'repo:app2',
      is_disabled: true,
      modified_spec: null,
      active_count: 0,
      total_executions: 3,
      total_tokens_used: 500,
      last_execution_at: null,
    },
  ]

  it('should group agents by repository name', async () => {
    const pool = mockPool([{ rows: repoAgentRows }])
    const ctx = makeCtx(pool)

    const result = await Query.repoAgentGroups(null, null, ctx)

    expect(result).toHaveLength(2)
    expect(result[0].repoName).toBe('app1')
    expect(result[0].agents).toHaveLength(2)
    expect(result[1].repoName).toBe('app2')
    expect(result[1].agents).toHaveLength(1)
  })

  it('should return empty array when no repo agents exist', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.repoAgentGroups(null, null, ctx)
    expect(result).toHaveLength(0)
  })

  it('should query with correct SQL filtering for repo sources', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.repoAgentGroups(null, null, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain("ad.source LIKE 'repo:%'")
    expect(sql).toContain('ad.is_active = true')
  })

  it('should correctly map agent definitions within groups', async () => {
    const pool = mockPool([{ rows: [repoAgentRows[2]] }])
    const ctx = makeCtx(pool)

    const result = await Query.repoAgentGroups(null, null, ctx)

    expect(result[0].agents[0].name).toBe('agent-c')
    expect(result[0].agents[0].source).toBe('REPOSITORY')
    expect(result[0].agents[0].sourceRepo).toBe('app2')
    expect(result[0].agents[0].isDisabled).toBe(true)
  })

  it('should handle single repo with multiple agents', async () => {
    const pool = mockPool([{ rows: [repoAgentRows[0], repoAgentRows[1]] }])
    const ctx = makeCtx(pool)

    const result = await Query.repoAgentGroups(null, null, ctx)

    expect(result).toHaveLength(1)
    expect(result[0].repoName).toBe('app1')
    expect(result[0].agents).toHaveLength(2)
    expect(result[0].agents[0].name).toBe('agent-a')
    expect(result[0].agents[1].name).toBe('agent-b')
  })
})
