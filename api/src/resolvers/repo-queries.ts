/**
 * Repository and auth-related GraphQL query resolvers.
 */

import { Context } from '../context.js'
import { mapRepository, mapAgentDefinition } from './queries.js'

export const repoQueries = {
  async repositories(_: unknown, __: unknown, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM repositories ORDER BY name ASC'
    )
    return result.rows.map(mapRepository)
  },

  async repository(_: unknown, args: { name: string }, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM repositories WHERE name = $1',
      [args.name]
    )
    if (result.rows.length === 0) return null
    return mapRepository(result.rows[0])
  },

  async agentInstances(_: unknown, __: unknown, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM agent_instances ORDER BY agent_name ASC'
    )
    return result.rows.map((row) => ({
      agentName: row.agent_name,
      activeCount: row.active_count,
      totalExecutions: row.total_executions,
      totalTokensUsed: row.total_tokens_used,
      lastExecutionAt: row.last_execution_at ?? null,
    }))
  },

  async globalAgents(_: unknown, __: unknown, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT
         ad.name, ad.version, ad.description, ad.spec, ad.source,
         COALESCE(ad.agent_group, 'pipeline') AS agent_group,
         COALESCE(ao.is_disabled, false) AS is_disabled,
         ao.modified_spec,
         COALESCE(ai.active_count, 0) AS active_count,
         COALESCE(ai.total_executions, 0) AS total_executions,
         COALESCE(ai.total_tokens_used, 0) AS total_tokens_used,
         ai.last_execution_at
       FROM agent_definitions ad
       LEFT JOIN agent_overrides ao
         ON ao.agent_name = ad.name AND ao.scope = 'global'
       LEFT JOIN agent_instances ai
         ON ai.agent_name = ad.name
       WHERE ad.is_active = true
         AND (ad.source = 'default' OR ad.source LIKE 'global:%')
       ORDER BY ad.name ASC`
    )
    return result.rows.map(mapAgentDefinition)
  },

  async pipelineDefinitions(_: unknown, __: unknown, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT name, version, stages, categories
       FROM pipeline_definitions
       WHERE is_active = true
       ORDER BY name ASC`
    )
    return result.rows.map((row) => {
      const rawStages = (row.stages as Array<Record<string, unknown>>) ?? []
      return {
        name: row.name,
        version: row.version,
        categories: row.categories ?? {},
        stages: rawStages.map((s) => {
          const conditions = (s.conditions as Array<Record<string, unknown>>) ?? []
          return {
            name: s.name ?? '',
            category: s.category as string,
            required: s.required ?? true,
            conditions: conditions.map((c) => {
              if (c.simple !== undefined) {
                return {
                  type: 'simple',
                  expression: String(c.simple),
                  onYes: c.yes !== undefined && c.yes !== null ? String(c.yes) : null,
                  onNo: c.no !== undefined && c.no !== null ? String(c.no) : null,
                  maxRepeats: c.maxRepeats !== undefined && c.maxRepeats !== null ? Number(c.maxRepeats) : null,
                }
              }
              if (c.ai !== undefined) {
                return {
                  type: 'ai',
                  expression: String(c.ai),
                  onYes: c.yes !== undefined && c.yes !== null ? String(c.yes) : null,
                  onNo: c.no !== undefined && c.no !== null ? String(c.no) : null,
                  maxRepeats: c.maxRepeats !== undefined && c.maxRepeats !== null ? Number(c.maxRepeats) : null,
                }
              }
              return {
                type: 'unknown',
                expression: JSON.stringify(c),
                onYes: null,
                onNo: null,
                maxRepeats: null,
              }
            }),
          }
        }),
      }
    })
  },

  async githubAuthStatus() {
    const { getAuthStatus } = await import('../github-auth.js')
    return getAuthStatus()
  },

  async githubRepositories() {
    const { listUserRepos } = await import('../github-auth.js')
    return listUserRepos()
  },

  async githubBranches(_: unknown, args: { owner: string; repo: string }) {
    const { listRepoBranches } = await import('../github-auth.js')
    return listRepoBranches(args.owner, args.repo)
  },

  async claudeAuthStatus() {
    const { getClaudeAuthStatus } = await import('../claude-auth.js')
    return getClaudeAuthStatus()
  },
}
