/**
 * Task-related GraphQL mutation resolvers.
 */

import crypto from 'node:crypto'
import { Context } from '../context.js'
import { toDbEnum, taskPayload, errorPayload } from './helpers.js'

export const taskMutations = {
  async createTask(
    _: unknown,
    args: {
      input: {
        title: string
        repository: string
        source: string
        sourceRef?: string | null
        pipeline?: string | null
        priority?: number | null
        initialContext?: unknown
      }
    },
    ctx: Context
  ) {
    const { input } = args

    // Verify repository exists
    const repoCheck = await ctx.pool.query(
      'SELECT name FROM repositories WHERE name = $1',
      [input.repository]
    )
    if (repoCheck.rows.length === 0) {
      return errorPayload('repository', `Repository "${input.repository}" not found`)
    }

    try {
      const id = `${input.source}-${crypto.randomUUID()}`
      const result = await ctx.pool.query<Record<string, unknown>>(
        `INSERT INTO tasks
           (id, title, repository, source, source_ref, pipeline, priority, initial_context, status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
         RETURNING *`,
        [
          id,
          input.title,
          input.repository,
          input.source,
          input.sourceRef ?? null,
          input.pipeline ?? 'feature-pipeline',
          input.priority ?? 5,
          input.initialContext ? JSON.stringify(input.initialContext) : null,
        ]
      )
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create task'
      return errorPayload(null, message)
    }
  },

  async updateTaskStatus(
    _: unknown,
    args: { id: string; status: string },
    ctx: Context
  ) {
    const dbStatus = toDbEnum(args.status)

    // Build timestamp updates based on target status
    let extraSql = ''
    const params: unknown[] = [dbStatus, args.id]

    if (dbStatus === 'executing') {
      extraSql = ', started_at = NOW()'
    } else if (dbStatus === 'completed' || dbStatus === 'failed' || dbStatus === 'cancelled') {
      extraSql = ', completed_at = NOW()'
    }

    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks SET status = $1, updated_at = NOW()${extraSql} WHERE id = $2 RETURNING *`,
        params
      )
      if (result.rows.length === 0) {
        return errorPayload('id', `Task "${args.id}" not found`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update task status'
      return errorPayload(null, message)
    }
  },

  async continueTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      // Reset the latest failed/rate_limited stage
      await ctx.pool.query(
        `UPDATE stages
         SET status = 'pending', error_message = NULL,
             started_at = NULL, completed_at = NULL,
             structured_output = NULL, raw_output = NULL, live_output = NULL
         WHERE task_id = $1 AND id = (
           SELECT id FROM stages WHERE task_id = $1
           AND status IN ('failed', 'rate_limited')
           ORDER BY stage_number DESC, run DESC LIMIT 1
         )`,
        [args.id]
      )

      // Reset task to pending — only if task is in a retryable status
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'pending',
             error_message = NULL,
             updated_at = NOW()
         WHERE id = $1
           AND status IN ('failed', 'rate_limited', 'blocked')
         RETURNING *`,
        [args.id]
      )
      if (result.rows.length === 0) {
        // Distinguish "not found" from "wrong status"
        const exists = await ctx.pool.query(
          'SELECT status FROM tasks WHERE id = $1',
          [args.id]
        )
        if (exists.rows.length === 0) {
          return errorPayload('id', `Task "${args.id}" not found`)
        }
        return errorPayload('id', `Task "${args.id}" cannot be retried in its current status`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to retry task'
      return errorPayload(null, message)
    }
  },

  async rerunTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      // Look up original task
      const original = await ctx.pool.query<Record<string, unknown>>(
        'SELECT * FROM tasks WHERE id = $1',
        [args.id]
      )
      if (original.rows.length === 0) {
        return errorPayload('id', `Task "${args.id}" not found`)
      }
      const orig = original.rows[0]
      const sourceRef = (orig.source_ref as string) || args.id

      // Atomic insert: compute rerun number inside the INSERT using a CTE
      // to avoid race conditions with concurrent rerunTask calls.
      const result = await ctx.pool.query<Record<string, unknown>>(
        `WITH locked_siblings AS (
           SELECT id FROM tasks WHERE parent_task_id = $1
           FOR UPDATE
         ),
         rerun_count AS (
           SELECT COUNT(*) + 1 AS n FROM locked_siblings
         )
         INSERT INTO tasks
           (id, title, source, source_ref, repository, pipeline,
            pipeline_version, initial_context, parent_task_id)
         SELECT
           $2 || '-rerun-' || rc.n,
           $3, $4, $5, $6, $7, $8, $9, $1
         FROM rerun_count rc
         RETURNING *`,
        [
          args.id,
          sourceRef,
          orig.title,
          orig.source,
          orig.source_ref,
          orig.repository,
          orig.pipeline,
          orig.pipeline_version ?? null,
          orig.initial_context ? JSON.stringify(orig.initial_context) : null,
        ]
      )
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to rerun task'
      return errorPayload(null, message)
    }
  },

  async closeTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      // Only close tasks that are not currently executing
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'closed', updated_at = NOW()
         WHERE id = $1
           AND status NOT IN ('executing')
         RETURNING *`,
        [args.id]
      )
      if (result.rows.length === 0) {
        // Distinguish "not found" from "wrong status"
        const exists = await ctx.pool.query(
          'SELECT status FROM tasks WHERE id = $1',
          [args.id]
        )
        if (exists.rows.length === 0) {
          return errorPayload('id', `Task "${args.id}" not found`)
        }
        return errorPayload('id', `Task "${args.id}" cannot be closed while executing`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to close task'
      return errorPayload(null, message)
    }
  },

  async cancelTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'cancelled',
             error_message = 'Task cancelled by user',
             completed_at = NOW(),
             updated_at = NOW()
         WHERE id = $1
           AND status NOT IN ('completed', 'failed', 'timeout', 'closed', 'cancelled')
         RETURNING *`,
        [args.id]
      )
      if (result.rows.length === 0) {
        const exists = await ctx.pool.query(
          'SELECT status FROM tasks WHERE id = $1',
          [args.id]
        )
        if (exists.rows.length === 0) {
          return errorPayload('id', `Task "${args.id}" not found`)
        }
        return errorPayload('id', `Task "${args.id}" is already in terminal status "${exists.rows[0].status}"`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to cancel task'
      return errorPayload(null, message)
    }
  },

  async unblockTask(
    _: unknown,
    args: { id: string; resolution: string },
    ctx: Context
  ) {
    // Verify task is currently BLOCKED
    const check = await ctx.pool.query<Record<string, unknown>>(
      'SELECT status FROM tasks WHERE id = $1',
      [args.id]
    )
    if (check.rows.length === 0) {
      return errorPayload('id', `Task "${args.id}" not found`)
    }
    if ((check.rows[0].status as string) !== 'blocked') {
      return errorPayload('id', `Task "${args.id}" is not blocked`)
    }

    try {
      // Insert resolution into context table, then set task back to PENDING
      await ctx.pool.query(
        `INSERT INTO context (task_id, key, value_type, value_text)
         VALUES ($1, 'unblock_resolution', 'text', $2)`,
        [args.id, args.resolution]
      )

      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'pending',
             error_message = NULL,
             updated_at = NOW()
         WHERE id = $1
         RETURNING *`,
        [args.id]
      )
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to unblock task'
      return errorPayload(null, message)
    }
  },
}
