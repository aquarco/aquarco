import { Pool } from 'pg'
import { Context } from '../context.js'
import { mapTask } from './mappers.js'

// GraphQL enum values are UPPER_CASE; DB stores lower_case
function toDbEnum(value: string | null | undefined): string | null {
  return value ? value.toLowerCase() : null
}

export const Query = {
  async tasks(
    _: unknown,
    args: {
      status?: string | null
      repository?: string | null
      limit?: number | null
      offset?: number | null
    },
    ctx: Context
  ) {
    const conditions: string[] = []
    const params: unknown[] = []
    let idx = 1

    if (args.status) {
      conditions.push(`status = $${idx++}`)
      params.push(toDbEnum(args.status))
    }
    if (args.repository) {
      conditions.push(`repository = $${idx++}`)
      params.push(args.repository)
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''

    const countResult = await ctx.pool.query<{ count: string }>(
      `SELECT COUNT(*) AS count FROM tasks ${where}`,
      params
    )
    const totalCount = parseInt(countResult.rows[0].count, 10)

    const limit = args.limit ?? 50
    const offset = args.offset ?? 0

    const dataResult = await ctx.pool.query<Record<string, unknown>>(
      `SELECT * FROM tasks ${where} ORDER BY created_at DESC LIMIT $${idx++} OFFSET $${idx++}`,
      [...params, limit, offset]
    )

    return {
      nodes: dataResult.rows.map(mapTask),
      totalCount,
    }
  },

  async task(_: unknown, args: { id: string }, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM tasks WHERE id = $1',
      [args.id]
    )
    if (result.rows.length === 0) return null
    return mapTask(result.rows[0])
  },

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

  async repoAgentGroups(_: unknown, __: unknown, ctx: Context) {
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
         ON ao.agent_name = ad.name AND ao.scope = ad.source
       LEFT JOIN agent_instances ai
         ON ai.agent_name = ad.name
       WHERE ad.is_active = true
         AND (ad.source LIKE 'repo:%' OR ad.source LIKE 'autoload:%')
       ORDER BY ad.source, ad.name ASC`
    )

    // Group by repository (handles both repo: and autoload: prefixes)
    const groupMap = new Map<string, Array<ReturnType<typeof mapAgentDefinition>>>()
    for (const row of result.rows) {
      const source = row.source as string
      const repoName = source.replace(/^(repo|autoload):/, '')
      if (!groupMap.has(repoName)) {
        groupMap.set(repoName, [])
      }
      groupMap.get(repoName)!.push(mapAgentDefinition(row))
    }

    return Array.from(groupMap.entries()).map(([repoName, agents]) => ({
      repoName,
      agents,
    }))
  },

  async repoAgentScan(_: unknown, args: { repoName: string }, ctx: Context) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT * FROM repo_agent_scans
       WHERE repo_name = $1
       ORDER BY created_at DESC
       LIMIT 1`,
      [args.repoName]
    )
    if (result.rows.length === 0) return null
    return mapRepoAgentScan(result.rows[0])
  },

  async pipelineStatus(_: unknown, args: { taskId: string }, ctx: Context) {
    const taskResult = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM tasks WHERE id = $1',
      [args.taskId]
    )
    if (taskResult.rows.length === 0) return null
    const task = taskResult.rows[0]

    const stageRows = await ctx.loaders.stagesByTaskLoader.load(args.taskId)
    const stages = stageRows.map((row) =>
      mapStage(row as unknown as Record<string, unknown>)
    )
    const uniqueStageNumbers = new Set(stageRows.map((r) => r.stage_number))
    const totalStages = uniqueStageNumbers.size

    return {
      taskId: task.id,
      pipeline: task.pipeline ?? null,
      currentStage: task.current_stage,
      totalStages,
      stages,
      status: (task.status as string).toUpperCase(),
    }
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

  async dashboardStats(_: unknown, __: unknown, ctx: Context) {
    const [totals, byPipeline, byRepo, agents, tokens] = await Promise.all([
      ctx.pool.query<Record<string, unknown>>(`
        SELECT
          COUNT(*) FILTER (WHERE TRUE) AS total,
          COUNT(*) FILTER (WHERE status = 'pending') AS pending,
          COUNT(*) FILTER (WHERE status = 'executing') AS executing,
          COUNT(*) FILTER (WHERE status = 'completed') AS completed,
          COUNT(*) FILTER (WHERE status = 'failed') AS failed,
          COUNT(*) FILTER (WHERE status = 'blocked') AS blocked
        FROM tasks
      `),
      ctx.pool.query<Record<string, unknown>>(
        `SELECT COALESCE(pipeline, 'feature-pipeline') AS pipeline, COUNT(*) AS count FROM tasks GROUP BY COALESCE(pipeline, 'feature-pipeline')`
      ),
      ctx.pool.query<Record<string, unknown>>(
        'SELECT repository, COUNT(*) AS count FROM tasks GROUP BY repository'
      ),
      ctx.pool.query<{ count: string }>(
        "SELECT COUNT(*) AS count FROM agent_instances WHERE active_count > 0"
      ),
      ctx.pool.query<{ total: string }>(`
        SELECT COALESCE(SUM(tokens_input + tokens_output), 0) AS total
        FROM stages
        WHERE started_at >= CURRENT_DATE
      `),
    ])

    const t = totals.rows[0]
    return {
      totalTasks: parseInt(t.total as string, 10),
      pendingTasks: parseInt(t.pending as string, 10),
      executingTasks: parseInt(t.executing as string, 10),
      completedTasks: parseInt(t.completed as string, 10),
      failedTasks: parseInt(t.failed as string, 10),
      blockedTasks: parseInt(t.blocked as string, 10),
      activeAgents: parseInt(agents.rows[0].count, 10),
      totalTokensToday: parseInt(tokens.rows[0].total, 10),
      tasksByPipeline: byPipeline.rows.map((r) => ({
        pipeline: r.pipeline as string,
        count: parseInt(r.count as string, 10),
      })),
      tasksByRepository: byRepo.rows.map((r) => ({
        repository: r.repository as string,
        count: parseInt(r.count as string, 10),
      })),
    }
  },
}

function parseAgentSource(source: string): { sourceEnum: string; sourceRepo: string | null } {
  if (source === 'default') return { sourceEnum: 'DEFAULT', sourceRepo: null }
  if (source.startsWith('global:')) return { sourceEnum: 'GLOBAL_CONFIG', sourceRepo: source.slice(7) }
  if (source.startsWith('repo:')) return { sourceEnum: 'REPOSITORY', sourceRepo: source.slice(5) }
  if (source.startsWith('autoload:')) return { sourceEnum: 'AUTOLOADED', sourceRepo: source.slice(9) }
  return { sourceEnum: 'DEFAULT', sourceRepo: null }
}

export function mapAgentDefinition(row: Record<string, unknown>) {
  const source = (row.source as string) ?? 'default'
  const { sourceEnum, sourceRepo } = parseAgentSource(source)
  const rawGroup = (row.agent_group as string | undefined) ?? 'pipeline'
  const group = rawGroup.toUpperCase() === 'SYSTEM' ? 'SYSTEM' : 'PIPELINE'
  return {
    name: row.name,
    version: row.version,
    description: row.description,
    source: sourceEnum,
    sourceRepo,
    group,
    spec: row.modified_spec ?? row.spec,
    isDisabled: row.is_disabled === true,
    isModified: row.modified_spec != null,
    modifiedSpec: row.modified_spec ?? null,
    activeCount: parseInt(String(row.active_count ?? '0'), 10),
    totalExecutions: parseInt(String(row.total_executions ?? '0'), 10),
    totalTokensUsed: parseInt(String(row.total_tokens_used ?? '0'), 10),
    lastExecutionAt: row.last_execution_at ?? null,
  }
}

export async function fetchAgentWithOverrides(
  pool: Pool,
  name: string,
  scope: string
): Promise<Record<string, unknown> | null> {
  const result = await pool.query<Record<string, unknown>>(
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
     LEFT JOIN agent_overrides ao ON ao.agent_name = ad.name AND ao.scope = $2
     LEFT JOIN agent_instances ai ON ai.agent_name = ad.name
     WHERE ad.name = $1 AND ad.is_active = true
     LIMIT 1`,
    [name, scope]
  )
  return result.rows[0] ?? null
}

export function mapRepository(row: Record<string, unknown>) {
  return {
    name: row.name,
    url: row.url,
    branch: row.branch,
    cloneDir: row.clone_dir,
    pollers: row.pollers ?? [],
    isConfigRepo: row.is_config_repo ?? false,
    lastClonedAt: row.last_cloned_at ?? null,
    lastPulledAt: row.last_pulled_at ?? null,
    cloneStatus: (row.clone_status as string).toUpperCase(),
    headSha: row.head_sha ?? null,
    errorMessage: row.error_message ?? null,
    deployPublicKey: row.deploy_public_key ?? null,
    // taskCount resolved by Repository field resolver
    _name: row.name,
  }
}

export function mapRepoAgentScan(row: Record<string, unknown>) {
  return {
    id: row.id,
    repoName: row.repo_name,
    status: (row.status as string).toUpperCase(),
    agentsFound: row.agents_found ?? 0,
    agentsCreated: row.agents_created ?? 0,
    errorMessage: row.error_message ?? null,
    startedAt: row.started_at ?? null,
    completedAt: row.completed_at ?? null,
    createdAt: row.created_at,
  }
}

export function mapStage(row: Record<string, unknown>) {
  return {
    id: row.id,
    taskId: row.task_id,
    stageNumber: row.stage_number,
    iteration: (row.iteration as number | null) ?? 1,
    run: (row.run as number | null) ?? 1,
    category: (row.category as string).toUpperCase(),
    agent: row.agent ?? null,
    agentVersion: row.agent_version ?? null,
    status: (row.status as string).toUpperCase(),
    startedAt: row.started_at ?? null,
    completedAt: row.completed_at ?? null,
    structuredOutput: row.structured_output ?? null,
    rawOutput: row.raw_output ?? null,
    tokensInput: row.tokens_input ?? null,
    tokensOutput: row.tokens_output ?? null,
    errorMessage: row.error_message ?? null,
    retryCount: row.retry_count,
    liveOutput: row.live_output ?? null,
  }
}
