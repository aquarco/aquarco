/**
 * Task-related GraphQL query resolvers.
 */

import { Context } from '../context.js'
import { mapTask, mapStage } from './mappers.js'
import { toDbEnum } from './helpers.js'

export const taskQueries = {
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
      lastCompletedStageId: task.last_completed_stage ?? null,
      totalStages,
      stages,
      status: (task.status as string).toUpperCase(),
    }
  },

  async dashboardStats(_: unknown, __: unknown, ctx: Context) {
    const [totals, byPipeline, byRepo, agents, tokens, cost] = await Promise.all([
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
        SELECT COALESCE(SUM(
          COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0) +
          COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)
        ), 0) AS total
        FROM stages
        WHERE started_at >= CURRENT_DATE
      `),
      ctx.pool.query<{ total: string }>(`
        SELECT COALESCE(SUM(cost_usd), 0) AS total
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
      totalCostToday: parseFloat(cost.rows[0].total),
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

  async tokenUsageByModel(
    _: unknown,
    args: { days?: number | null },
    ctx: Context
  ) {
    const days = Math.min(Math.max(args.days ?? 30, 1), 365)
    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT
         DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS day,
         COALESCE(model, 'unknown') AS model,
         COALESCE(SUM(tokens_input), 0)::int AS tokens_input,
         COALESCE(SUM(tokens_output), 0)::int AS tokens_output,
         COALESCE(SUM(cache_read_tokens), 0)::int AS cache_read_tokens,
         COALESCE(SUM(cache_write_tokens), 0)::int AS cache_write_tokens,
         COALESCE(SUM(cost_usd), 0)::float AS cost_usd
       FROM stages
       WHERE started_at >= NOW() - ($1 || ' days')::INTERVAL
       GROUP BY 1, 2
       ORDER BY 1 ASC, 2 ASC`,
      [String(days)]
    )
    return result.rows.map((row) => ({
      day: (row.day as Date).toISOString(),
      model: row.model as string,
      tokensInput: row.tokens_input as number,
      tokensOutput: row.tokens_output as number,
      cacheReadTokens: row.cache_read_tokens as number,
      cacheWriteTokens: row.cache_write_tokens as number,
      costUsd: row.cost_usd as number,
    }))
  },
}
