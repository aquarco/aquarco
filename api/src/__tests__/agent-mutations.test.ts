/**
 * Tests for agent override mutations (api/src/resolvers/mutations.ts)
 *
 * Covers acceptance criteria:
 *   - setAgentDisabled creates/updates agent_overrides row and reflects isDisabled
 *   - modifyAgent persists modified_spec and sets isModified true
 *   - modifyAgent rejects default agents with an error
 *   - resetAgentModification removes override and reverts to base spec
 *   - createAgentPR returns error when GitHub token is unavailable
 */

import { jest, describe, it, expect } from '@jest/globals'
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
    req: { headers: {} } as unknown as Context['req'],
  }
}

/** A complete agent definition row returned from the join query after mutations. */
const baseAgentRow: Record<string, unknown> = {
  name: 'test-agent',
  version: '1.0.0',
  description: 'A test agent',
  spec: { timeout: 300, maxTurns: 50 },
  source: 'global:config-repo',
  is_disabled: false,
  modified_spec: null,
  active_count: 0,
  total_executions: 10,
  total_tokens_used: 5000,
  last_execution_at: null,
}

// ── setAgentDisabled ───────────────────────────────────────────────────────────

describe('Mutation.setAgentDisabled', () => {
  it('should return agent with isDisabled=true after disabling', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent' }] },                        // agent exists check
      { rows: [] },                                                // upsert override
      { rows: [{ ...baseAgentRow, is_disabled: true }] },        // return updated agent
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(
      null,
      { name: 'test-agent', scope: 'global', disabled: true },
      ctx
    )

    expect(result.errors).toHaveLength(0)
    expect(result.agent).not.toBeNull()
    expect(result.agent!.isDisabled).toBe(true)
  })

  it('should return agent with isDisabled=false after enabling', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent' }] },
      { rows: [] },
      { rows: [{ ...baseAgentRow, is_disabled: false }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(
      null,
      { name: 'test-agent', scope: 'global', disabled: false },
      ctx
    )

    expect(result.errors).toHaveLength(0)
    expect(result.agent!.isDisabled).toBe(false)
  })

  it('should perform upsert with correct params', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent' }] },
      { rows: [] },
      { rows: [baseAgentRow] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.setAgentDisabled(
      null,
      { name: 'test-agent', scope: 'repo:my-app', disabled: true },
      ctx
    )

    // Second call is the upsert
    const upsertSql = pool.query.mock.calls[1][0] as string
    expect(upsertSql).toContain('agent_overrides')
    expect(upsertSql).toContain('ON CONFLICT')
    expect(upsertSql).toContain('is_disabled')
    const upsertParams = pool.query.mock.calls[1][1] as unknown[]
    expect(upsertParams).toEqual(['test-agent', 'repo:my-app', true])
  })

  it('should return error when agent does not exist', async () => {
    const pool = mockPool([
      { rows: [] }, // agent not found
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(
      null,
      { name: 'nonexistent', scope: 'global', disabled: true },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('nonexistent')
    expect(result.errors[0].message).toContain('not found')
  })

  it('should return error on DB failure', async () => {
    const pool = mockPool([{ rows: [{ name: 'test-agent' }] }])
    pool.query
      .mockResolvedValueOnce({ rows: [{ name: 'test-agent' }] })
      .mockRejectedValueOnce(new Error('connection timeout'))
    const ctx = makeCtx(pool)

    const result = await Mutation.setAgentDisabled(
      null,
      { name: 'test-agent', scope: 'global', disabled: true },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors[0].message).toContain('connection timeout')
  })
})

// ── modifyAgent ────────────────────────────────────────────────────────────────

describe('Mutation.modifyAgent', () => {
  const newSpec = { categories: ['review'], promptFile: 'agents/review.md' }

  it('should return agent with isModified=true after modification', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent', source: 'global:config-repo' }] },  // agent check
      { rows: [] },                                                        // upsert
      { rows: [{ ...baseAgentRow, modified_spec: newSpec }] },            // return
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.modifyAgent(
      null,
      { name: 'test-agent', scope: 'global', spec: newSpec },
      ctx
    )

    expect(result.errors).toHaveLength(0)
    expect(result.agent!.isModified).toBe(true)
    expect(result.agent!.modifiedSpec).toEqual(newSpec)
  })

  it('should use modified_spec as spec when modified', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent', source: 'global:config-repo' }] },
      { rows: [] },
      { rows: [{ ...baseAgentRow, modified_spec: newSpec }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.modifyAgent(
      null,
      { name: 'test-agent', scope: 'global', spec: newSpec },
      ctx
    )

    // spec should be the modified spec
    expect(result.agent!.spec).toEqual(newSpec)
  })

  it('should reject modification of default agents', async () => {
    const pool = mockPool([
      { rows: [{ name: 'analyze-agent', source: 'default' }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.modifyAgent(
      null,
      { name: 'analyze-agent', scope: 'global', spec: newSpec },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('Default agents cannot be modified')
  })

  it('should return error when agent does not exist', async () => {
    const pool = mockPool([
      { rows: [] },
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.modifyAgent(
      null,
      { name: 'ghost-agent', scope: 'global', spec: newSpec },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('not found')
  })

  it('should upsert modified_spec as JSON string', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent', source: 'repo:my-app' }] },
      { rows: [] },
      { rows: [baseAgentRow] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.modifyAgent(
      null,
      { name: 'test-agent', scope: 'repo:my-app', spec: newSpec },
      ctx
    )

    const upsertSql = pool.query.mock.calls[1][0] as string
    expect(upsertSql).toContain('modified_spec')
    expect(upsertSql).toContain('ON CONFLICT')
    const upsertParams = pool.query.mock.calls[1][1] as unknown[]
    expect(upsertParams[0]).toBe('test-agent')
    expect(upsertParams[1]).toBe('repo:my-app')
    expect(upsertParams[2]).toBe(JSON.stringify(newSpec))
  })

  it('should return error on DB failure', async () => {
    const pool = mockPool([
      { rows: [{ name: 'test-agent', source: 'global:config-repo' }] },
    ])
    pool.query
      .mockResolvedValueOnce({ rows: [{ name: 'test-agent', source: 'global:config-repo' }] })
      .mockRejectedValueOnce(new Error('disk full'))
    const ctx = makeCtx(pool)

    const result = await Mutation.modifyAgent(
      null,
      { name: 'test-agent', scope: 'global', spec: newSpec },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors[0].message).toContain('disk full')
  })
})

// ── resetAgentModification ─────────────────────────────────────────────────────

describe('Mutation.resetAgentModification', () => {
  it('should return agent reverted to base spec after reset', async () => {
    const pool = mockPool([
      { rows: [] },                                            // DELETE override
      { rows: [{ ...baseAgentRow, modified_spec: null }] },   // return base agent
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentModification(
      null,
      { name: 'test-agent', scope: 'global' },
      ctx
    )

    expect(result.errors).toHaveLength(0)
    expect(result.agent!.isModified).toBe(false)
    expect(result.agent!.modifiedSpec).toBeNull()
    expect(result.agent!.spec).toEqual({ timeout: 300, maxTurns: 50 })
  })

  it('should delete override from agent_overrides table', async () => {
    const pool = mockPool([
      { rows: [] },
      { rows: [baseAgentRow] },
    ])
    const ctx = makeCtx(pool)

    await Mutation.resetAgentModification(
      null,
      { name: 'test-agent', scope: 'repo:my-app' },
      ctx
    )

    const deleteSql = pool.query.mock.calls[0][0] as string
    expect(deleteSql).toContain('DELETE FROM agent_overrides')
    const deleteParams = pool.query.mock.calls[0][1] as unknown[]
    expect(deleteParams).toEqual(['test-agent', 'repo:my-app'])
  })

  it('should return isDisabled=false after reset (override removed)', async () => {
    const pool = mockPool([
      { rows: [] },
      { rows: [{ ...baseAgentRow, is_disabled: false, modified_spec: null }] },
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentModification(
      null,
      { name: 'test-agent', scope: 'global' },
      ctx
    )

    expect(result.agent!.isDisabled).toBe(false)
  })

  it('should return error when agent not found after delete', async () => {
    const pool = mockPool([
      { rows: [] },  // DELETE (succeeds even for nonexistent)
      { rows: [] },  // SELECT returns nothing
    ])
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentModification(
      null,
      { name: 'ghost-agent', scope: 'global' },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors[0].field).toBe('name')
    expect(result.errors[0].message).toContain('not found')
  })

  it('should return error on DB failure', async () => {
    const pool = mockPool([])
    pool.query.mockRejectedValueOnce(new Error('connection lost'))
    const ctx = makeCtx(pool)

    const result = await Mutation.resetAgentModification(
      null,
      { name: 'test-agent', scope: 'global' },
      ctx
    )

    expect(result.agent).toBeNull()
    expect(result.errors[0].message).toContain('connection lost')
  })
})

// ── createAgentPR ──────────────────────────────────────────────────────────────
// The createAgentPR mutation dynamically imports '../github-api.js' which reads
// a token file and calls GitHub APIs. Since this cannot be unit-tested without
// heavy mocking of fs and fetch, we test that the mutation correctly wraps errors
// from the dynamic import into the expected prErrorPayload shape.

describe('Mutation.createAgentPR', () => {
  it('should return error payload when github-api throws (no token file)', async () => {
    // createAgentPR does: const { createBranchAndPR } = await import('../github-api.js')
    // github-api.ts reads /agent-ssh/github-token which doesn't exist in test env
    const pool = mockPool([])
    const ctx = makeCtx(pool)

    const result = await Mutation.createAgentPR(null, { repoName: 'my-repo' }, ctx)

    // Should gracefully return an error payload, not throw
    expect(result.prUrl).toBeNull()
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0].field).toBeNull()
    expect(typeof result.errors[0].message).toBe('string')
    expect(result.errors[0].message.length).toBeGreaterThan(0)
  })

  it('should have correct payload shape with prUrl and errors fields', async () => {
    const pool = mockPool([])
    const ctx = makeCtx(pool)

    const result = await Mutation.createAgentPR(null, { repoName: 'test-repo' }, ctx)

    // Verify the error payload shape matches CreatePRPayload
    expect(result).toHaveProperty('prUrl')
    expect(result).toHaveProperty('errors')
    expect(Array.isArray(result.errors)).toBe(true)
  })
})
