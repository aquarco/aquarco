import { Context } from '../context.js'
import { mapRepository, mapStage } from './queries.js'

// GraphQL enum values are UPPER_CASE; DB stores lower_case
function toDbEnum(value: string): string {
  return value.toLowerCase()
}

function mapTask(row: Record<string, unknown>) {
  return {
    id: row.id,
    title: row.title,
    category: (row.category as string).toUpperCase(),
    status: (row.status as string).toUpperCase(),
    priority: row.priority,
    source: row.source,
    sourceRef: row.source_ref ?? null,
    pipeline: row.pipeline ?? null,
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

function taskPayload(task: Record<string, unknown>) {
  return { task: mapTask(task), errors: [] }
}

function errorPayload(field: string | null, message: string) {
  return { task: null, errors: [{ field, message }] }
}

function repoErrorPayload(field: string | null, message: string) {
  return { repository: null, errors: [{ field, message }] }
}

export const Mutation = {
  async githubLoginStart() {
    const { startDeviceFlow } = await import('../github-auth.js')
    return startDeviceFlow()
  },

  async githubLoginPoll() {
    const { pollDeviceFlow } = await import('../github-auth.js')
    return pollDeviceFlow()
  },

  async githubLogout() {
    const { logout } = await import('../github-auth.js')
    return logout()
  },

  async claudeLoginStart() {
    const { startClaudeLogin } = await import('../claude-auth.js')
    return startClaudeLogin()
  },

  async claudeLoginPoll() {
    const { pollClaudeLogin } = await import('../claude-auth.js')
    return pollClaudeLogin()
  },

  async claudeSubmitCode(_: unknown, args: { code: string }) {
    const { submitClaudeCode } = await import('../claude-auth.js')
    const result = await submitClaudeCode(args.code)
    return { success: result.success, email: null, error: result.error }
  },

  async claudeLogout() {
    const { claudeLogout } = await import('../claude-auth.js')
    return claudeLogout()
  },

  async createTask(
    _: unknown,
    args: {
      input: {
        title: string
        category: string
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
      const result = await ctx.pool.query<Record<string, unknown>>(
        `INSERT INTO tasks
           (title, category, repository, source, source_ref, pipeline, priority, initial_context, status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
         RETURNING *`,
        [
          input.title,
          toDbEnum(input.category),
          input.repository,
          input.source,
          input.sourceRef ?? null,
          input.pipeline ?? null,
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
    } else if (dbStatus === 'completed' || dbStatus === 'failed') {
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

  async retryTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'pending',
             retry_count = retry_count + 1,
             error_message = NULL,
             started_at = NULL,
             completed_at = NULL,
             updated_at = NOW()
         WHERE id = $1
         RETURNING *`,
        [args.id]
      )
      if (result.rows.length === 0) {
        return errorPayload('id', `Task "${args.id}" not found`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to retry task'
      return errorPayload(null, message)
    }
  },

  async cancelTask(_: unknown, args: { id: string }, ctx: Context) {
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE tasks
         SET status = 'failed',
             error_message = 'Task cancelled by user',
             completed_at = NOW(),
             updated_at = NOW()
         WHERE id = $1
         RETURNING *`,
        [args.id]
      )
      if (result.rows.length === 0) {
        return errorPayload('id', `Task "${args.id}" not found`)
      }
      return taskPayload(result.rows[0])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to cancel task'
      return errorPayload(null, message)
    }
  },

  async registerRepository(
    _: unknown,
    args: {
      input: {
        name: string
        url: string
        branch?: string | null
        cloneDir?: string | null
        pollers?: string[] | null
      }
    },
    ctx: Context
  ) {
    const { input } = args

    const safeDirName = input.name.replace(/[^a-zA-Z0-9._-]/g, '-')
    const reposBase = process.env.REPOS_BASE ?? '/home/agent/repos'
    const path = await import('node:path')

    const relative = input.cloneDir?.trim() || safeDirName
    const cloneDir = path.resolve(reposBase, relative.replace(/[^a-zA-Z0-9._\/-]/g, '-'))
    if (!cloneDir.startsWith(reposBase + '/')) {
      return repoErrorPayload('cloneDir', 'Invalid clone directory')
    }

    try {
      const trimmedUrl = input.url.trim()
      const result = await ctx.pool.query<Record<string, unknown>>(
        `INSERT INTO repositories (name, url, original_url, branch, clone_dir, pollers, clone_status)
         VALUES ($1, $2, $3, $4, $5, $6, 'pending')
         RETURNING *`,
        [
          input.name,
          trimmedUrl,
          trimmedUrl,
          input.branch ?? 'main',
          cloneDir,
          input.pollers ?? [],
        ]
      )
      return { repository: mapRepository(result.rows[0]), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to register repository'
      // Unique violation
      if (err instanceof Error && err.message.includes('unique')) {
        return repoErrorPayload('name', `Repository "${input.name}" already exists`)
      }
      return repoErrorPayload(null, message)
    }
  },

  async retryClone(_: unknown, args: { name: string }, ctx: Context) {
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE repositories
         SET clone_status = 'pending',
             error_message = NULL,
             url = COALESCE(original_url, url)
         WHERE name = $1
         RETURNING *`,
        [args.name]
      )
      if (result.rows.length === 0) {
        return repoErrorPayload('name', `Repository "${args.name}" not found`)
      }
      return { repository: mapRepository(result.rows[0]), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to retry clone'
      return repoErrorPayload(null, message)
    }
  },

  async removeRepository(_: unknown, args: { name: string }, ctx: Context) {
    // Check for referencing tasks
    const taskCheck = await ctx.pool.query<{ count: string }>(
      'SELECT COUNT(*) AS count FROM tasks WHERE repository = $1',
      [args.name]
    )
    if (parseInt(taskCheck.rows[0].count, 10) > 0) {
      return repoErrorPayload(
        'name',
        `Cannot remove repository "${args.name}" — it has tasks referencing it`
      )
    }

    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        'DELETE FROM repositories WHERE name = $1 RETURNING *',
        [args.name]
      )
      if (result.rows.length === 0) {
        return repoErrorPayload('name', `Repository "${args.name}" not found`)
      }

      const fs = await import('node:fs/promises')
      const path = await import('node:path')

      // Clean up the cloned directory on disk
      const cloneDir = result.rows[0].clone_dir as string
      if (cloneDir) {
        const reposBase = process.env.REPOS_BASE ?? '/home/agent/repos'
        // Map host path to container mount: /home/agent/repos/X -> /repos/X
        const relative = path.relative(reposBase, cloneDir)
        const containerPath = path.join('/repos', relative)
        // Safety: only delete if it's under /repos
        if (!relative.startsWith('..') && relative.length > 0) {
          await fs.rm(containerPath, { recursive: true, force: true }).catch(() => {})
        }
      }

      // Clean up deploy key pair (keyed by sanitized URL, matches clone-worker logic)
      const repoUrlStr = result.rows[0].url as string
      const urlKeyName = repoUrlStr
        .replace(/^git@/, '')
        .replace(/^https?:\/\//, '')
        .replace(/\.git$/, '')
        .replace(/[/:]/g, '-')
        .replace(/[^a-zA-Z0-9._-]/g, '-')
      if (urlKeyName) {
        await fs.rm(`/agent-ssh/deploy-keys/${urlKeyName}`, { recursive: true, force: true }).catch(() => {})
      }

      return { repository: mapRepository(result.rows[0]), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to remove repository'
      return repoErrorPayload(null, message)
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
