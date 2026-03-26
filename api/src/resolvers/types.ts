import { Context } from '../context.js'
import { RepositoryRow, StageRow, ContextRow } from '../loaders.js'
import { mapStage, mapRepoAgentScan } from './queries.js'

// ---- Scalar resolvers ----

export const DateTime = {
  serialize(value: unknown): string {
    if (value instanceof Date) return value.toISOString()
    return String(value)
  },
  parseValue(value: unknown): string {
    return String(value)
  },
  parseLiteral(ast: { value: string }): string {
    return ast.value
  },
}

export const JSON_Scalar = {
  serialize(value: unknown): unknown {
    return value
  },
  parseValue(value: unknown): unknown {
    return value
  },
  parseLiteral(ast: Record<string, unknown>): unknown {
    return ast.value ?? null
  },
}

// ---- Task field resolvers ----

export const Task = {
  // The parent object carries _repositoryName set by query/mutation resolvers
  async repository(
    parent: { _repositoryName: string },
    _: unknown,
    ctx: Context
  ): Promise<RepositoryRow | null> {
    return ctx.loaders.repositoryLoader.load(parent._repositoryName)
  },

  async stages(
    parent: { id: string },
    _: unknown,
    ctx: Context
  ) {
    const rows: StageRow[] = await ctx.loaders.stagesByTaskLoader.load(parent.id as string)
    return rows.map((row) =>
      mapStage(row as unknown as Record<string, unknown>)
    )
  },

  async context(
    parent: { id: string },
    _: unknown,
    ctx: Context
  ) {
    const rows: ContextRow[] = await ctx.loaders.contextByTaskLoader.load(parent.id as string)
    return rows.map((row) => ({
      id: row.id,
      taskId: row.task_id,
      stageNumber: row.stage_number ?? null,
      key: row.key,
      valueType: row.value_type,
      valueJson: row.value_json ?? null,
      valueText: row.value_text ?? null,
      valueFileRef: row.value_file_ref ?? null,
      createdAt: row.created_at,
    }))
  },
}

// ---- Repository field resolvers ----

export const Repository = {
  // Map snake_case loader fields to camelCase GraphQL fields
  // cloneDir, lastClonedAt, lastPulledAt, cloneStatus, headSha
  // are already mapped by mapRepository in queries.ts

  async taskCount(
    parent: { name: string } | RepositoryRow,
    _: unknown,
    ctx: Context
  ): Promise<number> {
    const result = await ctx.pool.query<{ count: string }>(
      'SELECT COUNT(*) AS count FROM tasks WHERE repository = $1',
      [parent.name]
    )
    return parseInt(result.rows[0].count, 10)
  },

  async hasClaudeAgents(
    parent: { name: string } | RepositoryRow,
    _: unknown,
    ctx: Context
  ): Promise<boolean> {
    // Check if autoloaded agents exist in DB for this repo
    const result = await ctx.pool.query<{ count: string }>(
      `SELECT COUNT(*) AS count FROM agent_definitions
       WHERE source = $1 AND is_active = true`,
      [`autoload:${parent.name}`]
    )
    if (parseInt(result.rows[0].count, 10) > 0) return true

    // Check if a successful scan found agents (even if definitions were later removed)
    const scanResult = await ctx.pool.query<{ agents_found: number }>(
      `SELECT agents_found FROM repo_agent_scans
       WHERE repo_name = $1 AND status = 'completed' AND agents_found > 0
       ORDER BY created_at DESC LIMIT 1`,
      [parent.name]
    )
    return scanResult.rows.length > 0
  },

  async lastAgentScan(
    parent: { name: string } | RepositoryRow,
    _: unknown,
    ctx: Context
  ) {
    const result = await ctx.pool.query<Record<string, unknown>>(
      `SELECT * FROM repo_agent_scans
       WHERE repo_name = $1
       ORDER BY created_at DESC
       LIMIT 1`,
      [parent.name]
    )
    if (result.rows.length === 0) return null
    return mapRepoAgentScan(result.rows[0])
  },
}
