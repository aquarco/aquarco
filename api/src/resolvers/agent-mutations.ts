/**
 * Agent override and PR-related GraphQL mutation resolvers.
 */

import { Context } from '../context.js'
import { mapAgentDefinition, fetchAgentWithOverrides } from './mappers.js'
import { agentErrorPayload, prErrorPayload, validateScope, validateSpec } from './helpers.js'

export const agentMutations = {
  async setAgentDisabled(
    _: unknown,
    args: { name: string; scope: string; disabled: boolean },
    ctx: Context
  ) {
    try {
      const scopeErr = validateScope(args.scope)
      if (scopeErr) return agentErrorPayload('scope', scopeErr)

      // Verify agent exists
      const agentCheck = await ctx.pool.query(
        'SELECT name FROM agent_definitions WHERE name = $1 AND is_active = true',
        [args.name]
      )
      if (agentCheck.rows.length === 0) {
        return agentErrorPayload('name', `Agent "${args.name}" not found`)
      }

      // Upsert the override
      await ctx.pool.query(
        `INSERT INTO agent_overrides (agent_name, scope, is_disabled)
         VALUES ($1, $2, $3)
         ON CONFLICT (agent_name, scope) DO UPDATE SET
           is_disabled = EXCLUDED.is_disabled`,
        [args.name, args.scope, args.disabled]
      )

      const row = await fetchAgentWithOverrides(ctx.pool, args.name, args.scope)
      if (!row) return agentErrorPayload('name', `Agent "${args.name}" not found`)
      return { agent: mapAgentDefinition(row), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update agent disabled state'
      return agentErrorPayload(null, message)
    }
  },

  async modifyAgent(
    _: unknown,
    args: { name: string; scope: string; spec: unknown },
    ctx: Context
  ) {
    try {
      const scopeErr = validateScope(args.scope)
      if (scopeErr) return agentErrorPayload('scope', scopeErr)

      const specErr = validateSpec(args.spec)
      if (specErr) return agentErrorPayload('spec', specErr)

      // Verify agent exists and is not default (default agents cannot be modified)
      const agentCheck = await ctx.pool.query<Record<string, unknown>>(
        'SELECT name, source FROM agent_definitions WHERE name = $1 AND is_active = true',
        [args.name]
      )
      if (agentCheck.rows.length === 0) {
        return agentErrorPayload('name', `Agent "${args.name}" not found`)
      }
      if (agentCheck.rows[0].source === 'default') {
        return agentErrorPayload('name', 'Default agents cannot be modified. You can only disable them.')
      }

      // Upsert the override with modified_spec
      await ctx.pool.query(
        `INSERT INTO agent_overrides (agent_name, scope, modified_spec)
         VALUES ($1, $2, $3)
         ON CONFLICT (agent_name, scope) DO UPDATE SET
           modified_spec = EXCLUDED.modified_spec`,
        [args.name, args.scope, JSON.stringify(args.spec)]
      )

      const row = await fetchAgentWithOverrides(ctx.pool, args.name, args.scope)
      if (!row) return agentErrorPayload('name', `Agent "${args.name}" not found`)
      return { agent: mapAgentDefinition(row), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to modify agent'
      return agentErrorPayload(null, message)
    }
  },

  async resetAgentModification(
    _: unknown,
    args: { name: string; scope: string },
    ctx: Context
  ) {
    try {
      const scopeErr = validateScope(args.scope)
      if (scopeErr) return agentErrorPayload('scope', scopeErr)

      // Delete the override row
      await ctx.pool.query(
        'DELETE FROM agent_overrides WHERE agent_name = $1 AND scope = $2',
        [args.name, args.scope]
      )

      // Override is deleted — LEFT JOIN naturally returns nulls
      const row = await fetchAgentWithOverrides(ctx.pool, args.name, args.scope)
      if (!row) return agentErrorPayload('name', `Agent "${args.name}" not found`)
      return { agent: mapAgentDefinition(row), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to reset agent modification'
      return agentErrorPayload(null, message)
    }
  },

  async createAgentPR(
    _: unknown,
    args: { repoName: string },
    ctx: Context
  ) {
    try {
      const { createBranchAndPR } = await import('../github-api.js')
      const prUrl = await createBranchAndPR(args.repoName, ctx.pool)
      return { prUrl, errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create PR'
      return prErrorPayload(message)
    }
  },
}
