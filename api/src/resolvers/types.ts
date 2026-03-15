import { Context } from '../context.js'
import { RepositoryRow, StageRow, ContextRow } from '../loaders.js'
import { mapStage } from './queries.js'

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
}
