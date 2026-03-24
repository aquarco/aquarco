import { Context } from '../context.js'

// GraphQL enum values are UPPER_CASE; DB stores lower_case
function toDbEnum(value: string | null | undefined): string | null {
  return value ? value.toLowerCase() : null
}

// Map a raw DB task row to the GraphQL Task shape (camelCase)
function mapTask(row: Record<string, unknown>) {
  return {
    id: row.id,
    title: row.title,
    status: (row.status as string).toUpperCase(),
    priority: row.priority,
    source: row.source,
    sourceRef: row.source_ref ?? null,
    pipeline: row.pipeline ?? 'feature-pipeline',
    // repository is resolved by the Task field resolver via DataLoader
    _repositoryName: row.repository,
    initialContext: row.initial_context ?? null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    startedAt: row.started_at ?? null,
    completedAt: row.completed_at ?? null,
    assignedAgent: row.assigned_agent ?? null,
    currentStage: row.current_stage,
    retryCount: row.retry_count,
    errorMessage: row.error_message ?? null,
  }
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

  async agentDefinitions(
    _: unknown,
    args: { source?: string | null },
    ctx: Context
  ) {
    let whereClause = 'WHERE ad.is_active = true'
    const params: unknown[] = []

    if (args.source) {
      params.push(args.source.toLowerCase())
      whereClause += ` AND ad.source = $${params.length}`
    }

    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT ad.*,
              ao.is_disabled AS override_disabled,
              ao.modified_spec AS override_modified_spec,
              ao.modified_at AS override_modified_at,
              CASE WHEN ao.id IS NOT NULL THEN true ELSE false END AS has_override
       FROM agent_definitions ad
       LEFT JOIN agent_overrides ao
         ON ao.agent_name = ad.name
         AND ao.scope = CASE
           WHEN ad.source = 'repository' THEN 'repository'
           ELSE 'global'
         END
         AND (ao.scope_repository = ad.source_repository OR (ao.scope_repository IS NULL AND ad.source_repository IS NULL))
       ${whereClause}
       ORDER BY ad.source, ad.name`,
      params
    )

    return result.rows.map(mapAgentDefinition)
  },

  async repositoriesWithAgents(_: unknown, __: unknown, ctx: Context) {
    // Get repos that have repo-specific agents
    const repoResult = await ctx.pool.query<Record<string, unknown>>(
      `SELECT DISTINCT r.*
       FROM repositories r
       INNER JOIN agent_definitions ad ON ad.source_repository = r.name AND ad.source = 'repository' AND ad.is_active = true
       ORDER BY r.name`
    )

    const repos = repoResult.rows.map(mapRepository)

    // For each repo, get its agents with overrides
    const result = []
    for (const repo of repos) {
      const agentResult = await ctx.pool.query<Record<string, unknown>>(
        `SELECT ad.*,
                ao.is_disabled AS override_disabled,
                ao.modified_spec AS override_modified_spec,
                ao.modified_at AS override_modified_at,
                CASE WHEN ao.id IS NOT NULL THEN true ELSE false END AS has_override
         FROM agent_definitions ad
         LEFT JOIN agent_overrides ao
           ON ao.agent_name = ad.name
           AND ao.scope = 'repository'
           AND ao.scope_repository = ad.source_repository
         WHERE ad.source = 'repository'
           AND ad.source_repository = $1
           AND ad.is_active = true
         ORDER BY ad.name`,
        [repo.name]
      )
      result.push({
        repository: repo,
        agents: agentResult.rows.map(mapAgentDefinition),
      })
    }

    return result
  },

  async pipelineStatus(_: unknown, args: { taskId: string }, ctx: Context) {
    const taskResult = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM tasks WHERE id = $1',
      [args.taskId]
    )
    if (taskResult.rows.length === 0) return null
    const task = taskResult.rows[0]

    const stagesResult = await ctx.pool.query<Record<string, unknown>>(
      'SELECT * FROM stages WHERE task_id = $1 ORDER BY stage_number ASC',
      [args.taskId]
    )

    const stages = stagesResult.rows.map(mapStage)
    const totalStages = stages.length

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

export function mapAgentDefinition(row: Record<string, unknown>) {
  return {
    name: row.name,
    version: row.version,
    description: row.description ?? null,
    source: (row.source as string).toUpperCase(),
    sourceRepository: row.source_repository ?? null,
    spec: row.override_modified_spec ?? row.spec,
    labels: row.labels ?? null,
    isActive: row.is_active ?? true,
    isDisabled: row.override_disabled ?? false,
    hasOverride: row.has_override ?? false,
    modifiedSpec: row.override_modified_spec ?? null,
  }
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

export function mapStage(row: Record<string, unknown>) {
  return {
    id: row.id,
    taskId: row.task_id,
    stageNumber: row.stage_number,
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
  }
}
