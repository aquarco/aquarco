/**
 * Tests for the agent definitions & overrides feature
 * (api/src/resolvers/queries.ts — agentDefinitions, repositoriesWithAgents, mapAgentDefinition)
 * (api/src/resolvers/mutations.ts — setAgentDisabled, updateAgentSpec, resetAgentOverride)
 *
 * Covers the "Redesign agents page" feature (GitHub issue #1).
 * All PostgreSQL pool interactions are mocked.
 */

import { jest, describe, it, expect } from '@jest/globals'
import { Query, mapAgentDefinition } from '../resolvers/queries.js'
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

// ── Test data ──────────────────────────────────────────────────────────────────

const baseAgentRow: Record<string, unknown> = {
  name: 'analyze-agent',
  version: '1.0.0',
  description: 'Analyzes issues and PRs',
  source: 'default',
  source_repository: null,
  spec: { tools: { allowed: ['Read', 'Grep'] }, resources: { timeoutMinutes: 15 } },
  labels: { category: 'analyze' },
  is_active: true,
  override_disabled: false,
  override_modified_spec: null,
  override_modified_at: null,
  has_override: false,
}

const globalConfigAgentRow: Record<string, unknown> = {
  name: 'custom-lint-agent',
  version: '2.0.0',
  description: 'Runs linting checks',
  source: 'global',
  source_repository: 'config-repo',
  spec: { tools: { allowed: ['Bash'] } },
  labels: null,
  is_active: true,
  override_disabled: false,
  override_modified_spec: null,
  override_modified_at: null,
  has_override: false,
}

const repoAgentRow: Record<string, unknown> = {
  name: 'repo-deploy-agent',
  version: '1.0.0',
  description: 'Deploys the repo',
  source: 'repository',
  source_repository: 'my-app',
  spec: { tools: { allowed: ['Bash', 'Read'] } },
  labels: null,
  is_active: true,
  override_disabled: true,
  override_modified_spec: null,
  override_modified_at: null,
  has_override: true,
}

const baseRepoRow: Record<string, unknown> = {
  name: 'my-app',
  url: 'https://github.com/org/my-app',
  branch: 'main',
  clone_dir: '/repos/my-app',
  pollers: ['github-issues'],
  is_config_repo: false,
  last_cloned_at: null,
  last_pulled_at: null,
  clone_status: 'ready',
  head_sha: 'abc123',
}

const baseOverrideRow: Record<string, unknown> = {
  id: 1,
  agent_name: 'analyze-agent',
  agent_version: '1.0.0',
  scope: 'global',
  scope_repository: null,
  is_disabled: true,
  modified_spec: null,
  modified_at: null,
  created_at: '2026-03-24T10:00:00Z',
  updated_at: '2026-03-24T10:00:00Z',
}

// ── mapAgentDefinition ─────────────────────────────────────────────────────────

describe('mapAgentDefinition', () => {
  it('should map a default agent row to GraphQL shape', () => {
    const result = mapAgentDefinition(baseAgentRow)

    expect(result.name).toBe('analyze-agent')
    expect(result.version).toBe('1.0.0')
    expect(result.description).toBe('Analyzes issues and PRs')
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepository).toBeNull()
    expect(result.spec).toEqual(baseAgentRow.spec)
    expect(result.labels).toEqual({ category: 'analyze' })
    expect(result.isActive).toBe(true)
    expect(result.isDisabled).toBe(false)
    expect(result.hasOverride).toBe(false)
    expect(result.modifiedSpec).toBeNull()
  })

  it('should uppercase the source field', () => {
    expect(mapAgentDefinition({ ...baseAgentRow, source: 'default' }).source).toBe('DEFAULT')
    expect(mapAgentDefinition({ ...baseAgentRow, source: 'global' }).source).toBe('GLOBAL')
    expect(mapAgentDefinition({ ...baseAgentRow, source: 'repository' }).source).toBe('REPOSITORY')
  })

  it('should use override_modified_spec as spec when present', () => {
    const overriddenSpec = { tools: { allowed: ['Bash'] } }
    const row = { ...baseAgentRow, override_modified_spec: overriddenSpec }
    const result = mapAgentDefinition(row)

    expect(result.spec).toEqual(overriddenSpec)
    expect(result.modifiedSpec).toEqual(overriddenSpec)
  })

  it('should use original spec when no override exists', () => {
    const result = mapAgentDefinition(baseAgentRow)
    expect(result.spec).toEqual(baseAgentRow.spec)
    expect(result.modifiedSpec).toBeNull()
  })

  it('should set isDisabled from override_disabled', () => {
    const disabledRow = { ...baseAgentRow, override_disabled: true, has_override: true }
    const result = mapAgentDefinition(disabledRow)
    expect(result.isDisabled).toBe(true)
    expect(result.hasOverride).toBe(true)
  })

  it('should handle null/undefined optional fields gracefully', () => {
    const minimalRow = {
      ...baseAgentRow,
      description: null,
      labels: null,
      source_repository: undefined,
      override_disabled: undefined,
      override_modified_spec: undefined,
      has_override: undefined,
    }
    const result = mapAgentDefinition(minimalRow)
    expect(result.description).toBeNull()
    expect(result.labels).toBeNull()
    expect(result.sourceRepository).toBeNull()
    expect(result.isDisabled).toBeFalsy()
    expect(result.hasOverride).toBeFalsy()
    expect(result.modifiedSpec).toBeNull()
  })

  it('should map a global config agent with sourceRepository', () => {
    const result = mapAgentDefinition(globalConfigAgentRow)
    expect(result.source).toBe('GLOBAL')
    expect(result.sourceRepository).toBe('config-repo')
  })

  it('should map a repository agent with disabled override', () => {
    const result = mapAgentDefinition(repoAgentRow)
    expect(result.source).toBe('REPOSITORY')
    expect(result.sourceRepository).toBe('my-app')
    expect(result.isDisabled).toBe(true)
    expect(result.hasOverride).toBe(true)
  })
})

// ── Query.agentDefinitions ─────────────────────────────────────────────────────

describe('Query.agentDefinitions', () => {
  it('should return all active agent definitions when no source filter', async () => {
    const pool = mockPool([{ rows: [baseAgentRow, globalConfigAgentRow] }])
    const ctx = makeCtx(pool)

    const result = await Query.agentDefinitions(null, {}, ctx)

    expect(result).toHaveLength(2)
    expect(result[0].name).toBe('analyze-agent')
    expect(result[1].name).toBe('custom-lint-agent')
  })

  it('should filter by source when provided', async () => {
    const pool = mockPool([{ rows: [baseAgentRow] }])
    const ctx = makeCtx(pool)

    await Query.agentDefinitions(null, { source: 'DEFAULT' }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('ad.source = $')
    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params).toContain('default')
  })

  it('should lowercase the source parameter for DB query', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.agentDefinitions(null, { source: 'GLOBAL' }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[0]).toBe('global')
  })

  it('should join with agent_overrides table', async () => {
    const pool = mockPool([{ rows: [baseAgentRow] }])
    const ctx = makeCtx(pool)

    await Query.agentDefinitions(null, {}, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('LEFT JOIN agent_overrides')
    expect(sql).toContain('ao.agent_name = ad.name')
  })

  it('should only return active agents', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.agentDefinitions(null, {}, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('ad.is_active = true')
  })

  it('should return empty array when no agents found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.agentDefinitions(null, {}, ctx)
    expect(result).toHaveLength(0)
  })

  it('should include has_override flag in result', async () => {
    const rowWithOverride = { ...baseAgentRow, has_override: true, override_disabled: true }
    const pool = mockPool([{ rows: [rowWithOverride] }])
    const ctx = makeCtx(pool)

    const result = await Query.agentDefinitions(null, {}, ctx)
    expect(result[0].hasOverride).toBe(true)
    expect(result[0].isDisabled).toBe(true)
  })

  it('should order results by source and name', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    await Query.agentDefinitions(null, {}, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('ORDER BY ad.source, ad.name')
  })
})

// ── Query.repositoriesWithAgents ───────────────────────────────────────────────

describe('Query.repositoriesWithAgents', () => {
  it('should return repos with their repository-specific agents', async () => {
    const pool = mockPool([
      { rows: [baseRepoRow] },           // repositories with agents
      { rows: [repoAgentRow] },          // agents for my-app
    ])
    const ctx = makeCtx(pool)

    const result = await Query.repositoriesWithAgents(null, null, ctx)

    expect(result).toHaveLength(1)
    expect(result[0].repository.name).toBe('my-app')
    expect(result[0].agents).toHaveLength(1)
    expect(result[0].agents[0].name).toBe('repo-deploy-agent')
  })

  it('should return empty array when no repos have custom agents', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Query.repositoriesWithAgents(null, null, ctx)
    expect(result).toHaveLength(0)
  })

  it('should only query for repository-source agents', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }, { rows: [] }])
    const ctx = makeCtx(pool)

    await Query.repositoriesWithAgents(null, null, ctx)

    // First query: repos with repo-specific agents
    const repoSql = pool.query.mock.calls[0][0] as string
    expect(repoSql).toContain("ad.source = 'repository'")
    expect(repoSql).toContain('ad.is_active = true')
  })

  it('should pass repository name when querying agents', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }, { rows: [repoAgentRow] }])
    const ctx = makeCtx(pool)

    await Query.repositoriesWithAgents(null, null, ctx)

    // Second query: agents for the repo
    const agentParams = pool.query.mock.calls[1][1] as unknown[]
    expect(agentParams[0]).toBe('my-app')
  })

  it('should handle multiple repositories', async () => {
    const secondRepoRow = { ...baseRepoRow, name: 'other-app', clone_dir: '/repos/other-app' }
    const secondAgentRow = { ...repoAgentRow, name: 'other-agent', source_repository: 'other-app' }
    const pool = mockPool([
      { rows: [baseRepoRow, secondRepoRow] },  // two repos
      { rows: [repoAgentRow] },                 // agents for my-app
      { rows: [secondAgentRow] },               // agents for other-app
    ])
    const ctx = makeCtx(pool)

    const result = await Query.repositoriesWithAgents(null, null, ctx)

    expect(result).toHaveLength(2)
    expect(result[0].repository.name).toBe('my-app')
    expect(result[1].repository.name).toBe('other-app')
  })

  it('should join overrides for repository-scoped agents', async () => {
    const pool = mockPool([{ rows: [baseRepoRow] }, { rows: [] }])
    const ctx = makeCtx(pool)

    await Query.repositoriesWithAgents(null, null, ctx)

    const agentSql = pool.query.mock.calls[1][0] as string
    expect(agentSql).toContain('LEFT JOIN agent_overrides')
    expect(agentSql).toContain("ao.scope = 'repository'")
  })
})

// ── Mutation.setAgentDisabled ──────────────────────────────────────────────────

describe('Mutation.setAgentDisabled', () => {
  it('should create/update override to disable an agent (global scope)', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(null, {
      input: { agentName: 'analyze-agent', isDisabled: true, scope: 'global', scopeRepository: null },
    }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.override).not.toBeNull()
    expect(result.override!.agentName).toBe('analyze-agent')
    expect(result.override!.isDisabled).toBe(true)
    expect(result.override!.scope).toBe('global')
  })

  it('should create override to enable an agent', async () => {
    const enabledRow = { ...baseOverrideRow, is_disabled: false }
    const pool = mockPool([{ rows: [enabledRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(null, {
      input: { agentName: 'analyze-agent', isDisabled: false, scope: 'global' },
    }, ctx)

    expect(result.override!.isDisabled).toBe(false)
  })

  it('should support repository-scoped disable', async () => {
    const repoOverrideRow = {
      ...baseOverrideRow,
      scope: 'repository',
      scope_repository: 'my-app',
    }
    const pool = mockPool([{ rows: [repoOverrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(null, {
      input: {
        agentName: 'repo-deploy-agent',
        isDisabled: true,
        scope: 'repository',
        scopeRepository: 'my-app',
      },
    }, ctx)

    expect(result.override!.scope).toBe('repository')
    expect(result.override!.scopeRepository).toBe('my-app')
  })

  it('should use UPSERT to handle conflicts', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.setAgentDisabled(null, {
      input: { agentName: 'analyze-agent', isDisabled: true, scope: 'global' },
    }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('ON CONFLICT')
    expect(sql).toContain('DO UPDATE SET is_disabled')
  })

  it('should verify agent exists via agent_definitions join', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.setAgentDisabled(null, {
      input: { agentName: 'analyze-agent', isDisabled: true, scope: 'global' },
    }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('FROM agent_definitions')
    expect(sql).toContain('ad.is_active = true')
  })

  it('should return error when agent not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(null, {
      input: { agentName: 'nonexistent-agent', isDisabled: true, scope: 'global' },
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].field).toBe('agentName')
    expect(result.errors[0].message).toContain('nonexistent-agent')
  })

  it('should return error payload on DB errors', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('connection lost'))
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(null, {
      input: { agentName: 'analyze-agent', isDisabled: true, scope: 'global' },
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors[0].message).toContain('connection lost')
  })
})

// ── Mutation.updateAgentSpec ──────────────────────────────────────────────────

describe('Mutation.updateAgentSpec', () => {
  const modifiedSpec = { tools: { allowed: ['Bash', 'Read', 'Write'] }, resources: { timeoutMinutes: 30 } }

  it('should save spec override for an agent (global scope)', async () => {
    const overrideRow = {
      ...baseOverrideRow,
      modified_spec: modifiedSpec,
      modified_at: '2026-03-24T10:30:00Z',
    }
    const pool = mockPool([{ rows: [overrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.updateAgentSpec(null, {
      input: { agentName: 'analyze-agent', spec: modifiedSpec, scope: 'global', scopeRepository: null },
    }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.override!.agentName).toBe('analyze-agent')
    expect(result.override!.modifiedSpec).toEqual(modifiedSpec)
    expect(result.override!.modifiedAt).toBe('2026-03-24T10:30:00Z')
  })

  it('should save spec override for repository-scoped agent', async () => {
    const overrideRow = {
      ...baseOverrideRow,
      scope: 'repository',
      scope_repository: 'my-app',
      modified_spec: modifiedSpec,
      modified_at: '2026-03-24T10:30:00Z',
    }
    const pool = mockPool([{ rows: [overrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.updateAgentSpec(null, {
      input: {
        agentName: 'repo-deploy-agent',
        spec: modifiedSpec,
        scope: 'repository',
        scopeRepository: 'my-app',
      },
    }, ctx)

    expect(result.override!.scope).toBe('repository')
    expect(result.override!.scopeRepository).toBe('my-app')
  })

  it('should stringify spec before sending to DB', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.updateAgentSpec(null, {
      input: { agentName: 'analyze-agent', spec: modifiedSpec, scope: 'global' },
    }, ctx)

    const params = pool.query.mock.calls[0][1] as unknown[]
    // The spec param (index 3) should be stringified
    expect(params[3]).toBe(JSON.stringify(modifiedSpec))
  })

  it('should use UPSERT with modified_spec and modified_at', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.updateAgentSpec(null, {
      input: { agentName: 'analyze-agent', spec: modifiedSpec, scope: 'global' },
    }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('ON CONFLICT')
    expect(sql).toContain('DO UPDATE SET modified_spec')
    expect(sql).toContain('modified_at = NOW()')
  })

  it('should return error when agent not found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.updateAgentSpec(null, {
      input: { agentName: 'ghost-agent', spec: modifiedSpec, scope: 'global' },
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors[0].field).toBe('agentName')
    expect(result.errors[0].message).toContain('ghost-agent')
  })

  it('should return error payload on DB errors', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('serialization failure'))
    const ctx = makeCtx(pool)

    const result = await Mutation.updateAgentSpec(null, {
      input: { agentName: 'analyze-agent', spec: modifiedSpec, scope: 'global' },
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors[0].message).toContain('serialization failure')
  })
})

// ── Mutation.resetAgentOverride ───────────────────────────────────────────────

describe('Mutation.resetAgentOverride', () => {
  it('should delete the override and return it', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentOverride(null, {
      agentName: 'analyze-agent',
      scope: 'global',
      scopeRepository: null,
    }, ctx)

    expect(result.errors).toHaveLength(0)
    expect(result.override!.agentName).toBe('analyze-agent')
    expect(result.override!.scope).toBe('global')
  })

  it('should use DELETE with correct WHERE conditions', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.resetAgentOverride(null, {
      agentName: 'analyze-agent',
      scope: 'global',
    }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('DELETE FROM agent_overrides')
    expect(sql).toContain('agent_name = $1')
    expect(sql).toContain('scope = $2')
  })

  it('should handle NULL scope_repository correctly', async () => {
    const pool = mockPool([{ rows: [baseOverrideRow] }])
    const ctx = makeCtx(pool)

    await Mutation.resetAgentOverride(null, {
      agentName: 'analyze-agent',
      scope: 'global',
      scopeRepository: null,
    }, ctx)

    const sql = pool.query.mock.calls[0][0] as string
    expect(sql).toContain('$3 IS NULL AND scope_repository IS NULL')
  })

  it('should support repository-scoped reset', async () => {
    const repoOverrideRow = {
      ...baseOverrideRow,
      scope: 'repository',
      scope_repository: 'my-app',
    }
    const pool = mockPool([{ rows: [repoOverrideRow] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentOverride(null, {
      agentName: 'repo-deploy-agent',
      scope: 'repository',
      scopeRepository: 'my-app',
    }, ctx)

    expect(result.override!.scopeRepository).toBe('my-app')
    const params = pool.query.mock.calls[0][1] as unknown[]
    expect(params[2]).toBe('my-app')
  })

  it('should return error when no override found', async () => {
    const pool = mockPool([{ rows: [] }])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentOverride(null, {
      agentName: 'no-override-agent',
      scope: 'global',
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors[0].field).toBe('agentName')
    expect(result.errors[0].message).toContain('No override found')
  })

  it('should return error payload on DB errors', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('deadlock detected'))
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentOverride(null, {
      agentName: 'analyze-agent',
      scope: 'global',
    }, ctx)

    expect(result.override).toBeNull()
    expect(result.errors[0].message).toContain('deadlock detected')
  })
})
