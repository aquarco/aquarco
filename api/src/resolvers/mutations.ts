import crypto from 'node:crypto'
import { Context, requireInternalAuth } from '../context.js'
import { mapRepository, mapStage, mapAgentDefinition, fetchAgentWithOverrides, getDrainStatus } from './queries.js'
import { mapTask } from './mappers.js'

// GraphQL enum values are UPPER_CASE; DB stores lower_case
function toDbEnum(value: string): string {
  return value.toLowerCase()
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

function agentErrorPayload(field: string | null, message: string) {
  return { agent: null, errors: [{ field, message }] }
}

const SCOPE_PATTERN = /^(global|repo:[a-zA-Z0-9._-]+)$/
function validateScope(scope: string): string | null {
  if (!SCOPE_PATTERN.test(scope)) return `Invalid scope "${scope}". Must be "global" or "repo:<name>".`
  return null
}

const VALID_SPEC_KEYS = new Set([
  'categories', 'priority', 'promptFile', 'promptInline', 'tools', 'resources',
  'environment', 'output', 'outputSchema', 'healthCheck', 'conditions',
])
const REQUIRED_SPEC_KEYS = ['categories']
const MAX_SPEC_SIZE = 100 * 1024

function validateSpec(spec: unknown): string | null {
  if (typeof spec !== 'object' || spec === null || Array.isArray(spec)) return 'Spec must be a JSON object'
  if (JSON.stringify(spec).length > MAX_SPEC_SIZE) return 'Spec exceeds 100KB size limit'
  const keys = Object.keys(spec)
  for (const k of REQUIRED_SPEC_KEYS) { if (!keys.includes(k)) return `Spec missing required key "${k}"` }
  for (const k of keys) { if (!VALID_SPEC_KEYS.has(k)) return `Spec contains unknown key "${k}"` }
  // Require at least one prompt source
  if (!keys.includes('promptFile') && !keys.includes('promptInline')) {
    return 'Spec must contain either "promptFile" or "promptInline"'
  }
  return null
}

function prErrorPayload(message: string) {
  return { prUrl: null, errors: [{ field: null, message }] }
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
         SET status = 'failed',
             error_message = 'Task cancelled by user',
             completed_at = NOW(),
             updated_at = NOW()
         WHERE id = $1
           AND status NOT IN ('completed', 'failed', 'timeout', 'closed')
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

  async registerRepository(
    _: unknown,
    args: {
      input: {
        name: string
        url: string
        branch?: string | null
        cloneDir?: string | null
        pollers?: string[] | null
        isConfigRepo?: boolean | null
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
        `INSERT INTO repositories (name, url, original_url, branch, clone_dir, pollers, is_config_repo, clone_status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
         RETURNING *`,
        [
          input.name,
          trimmedUrl,
          trimmedUrl,
          input.branch || null,
          cloneDir,
          input.pollers ?? [],
          input.isConfigRepo ?? false,
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

  async setConfigRepo(
    _: unknown,
    args: { name: string; isConfigRepo: boolean },
    ctx: Context
  ) {
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE repositories SET is_config_repo = $1 WHERE name = $2 RETURNING *`,
        [args.isConfigRepo, args.name]
      )
      if (result.rows.length === 0) {
        return repoErrorPayload('name', `Repository "${args.name}" not found`)
      }
      return { repository: mapRepository(result.rows[0]), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update repository'
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

  // --- Agent override mutations ---

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

  async setDrainMode(
    _: unknown,
    args: { enabled: boolean },
    ctx: Context
  ) {
    requireInternalAuth(ctx)
    const value = args.enabled ? 'true' : 'false'
    await ctx.pool.query(
      `INSERT INTO supervisor_state (key, value, updated_at)
       VALUES ('drain_mode', $1, NOW())
       ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()`,
      [value]
    )
    return getDrainStatus(ctx.pool)
  },
}
