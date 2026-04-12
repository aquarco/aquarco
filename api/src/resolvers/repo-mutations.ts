/**
 * Repository-related GraphQL mutation resolvers.
 */

import { Context, requireInternalAuth } from '../context.js'
import { mapRepository, getDrainStatus } from './mappers.js'
import { repoErrorPayload } from './helpers.js'

type BranchRuleInput = {
  issueLabels?: string[] | null
  baseBranch?: string | null
  pipeline?: string | null
}

type GitFlowConfigInput = {
  enabled: boolean
  branches?: {
    stable?: string | null
    development?: string | null
    release?: string | null
    feature?: string | null
    bugfix?: string | null
    hotfix?: string | null
  } | null
  rules?: {
    feature?: BranchRuleInput | null
    bugfix?: BranchRuleInput | null
    hotfix?: BranchRuleInput | null
    branchNameOverride?: string | null
  } | null
} | null

function serializeBranchRule(rule: BranchRuleInput | null | undefined) {
  if (!rule) return null
  return {
    issueLabels: rule.issueLabels ?? [],
    baseBranch: rule.baseBranch ?? 'development',
    pipeline: rule.pipeline ?? null,
  }
}

function serializeGitFlowConfig(config: GitFlowConfigInput): string | null {
  if (!config) return null
  return JSON.stringify({
    enabled: config.enabled,
    branches: {
      stable: config.branches?.stable ?? 'main',
      development: config.branches?.development ?? 'develop',
      release: config.branches?.release ?? 'release/*',
      feature: config.branches?.feature ?? 'feature/*',
      bugfix: config.branches?.bugfix ?? 'bugfix/*',
      hotfix: config.branches?.hotfix ?? 'hotfix/*',
    },
    rules: config.rules ? {
      feature: serializeBranchRule(config.rules.feature),
      bugfix: serializeBranchRule(config.rules.bugfix),
      hotfix: serializeBranchRule(config.rules.hotfix),
      branchNameOverride: config.rules.branchNameOverride ?? null,
    } : null,
  })
}

export const repoMutations = {
  async registerRepository(
    _: unknown,
    args: {
      input: {
        name: string
        url: string
        branch?: string | null
        cloneDir?: string | null
        pollers?: string[] | null
        gitFlowConfig?: GitFlowConfigInput
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
      const gitFlowConfigValue = serializeGitFlowConfig(input.gitFlowConfig ?? null)
      const result = await ctx.pool.query<Record<string, unknown>>(
        `INSERT INTO repositories (name, url, original_url, branch, clone_dir, pollers, clone_status, git_flow_config)
         VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
         RETURNING *`,
        [
          input.name,
          trimmedUrl,
          trimmedUrl,
          input.branch || null,
          cloneDir,
          input.pollers ?? [],
          gitFlowConfigValue,
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

  async updateRepository(
    _: unknown,
    args: {
      name: string
      input: {
        url?: string | null
        branch?: string | null
        pollers?: string[] | null
        gitFlowConfig?: GitFlowConfigInput
      }
    },
    ctx: Context
  ) {
    const { name, input } = args
    const setClauses: string[] = []
    const values: unknown[] = []

    if (input.url !== undefined && input.url !== null) {
      values.push(input.url.trim())
      setClauses.push(`url = $${values.length}`)
    }
    if (input.branch !== undefined) {
      values.push(input.branch || null)
      setClauses.push(`branch = $${values.length}`)
    }
    if (input.pollers !== undefined && input.pollers !== null) {
      values.push(input.pollers)
      setClauses.push(`pollers = $${values.length}`)
    }
    if ('gitFlowConfig' in input) {
      values.push(serializeGitFlowConfig(input.gitFlowConfig ?? null))
      setClauses.push(`git_flow_config = $${values.length}`)
    }

    if (setClauses.length === 0) {
      const row = await ctx.pool.query<Record<string, unknown>>(
        'SELECT * FROM repositories WHERE name = $1',
        [name]
      )
      if (row.rows.length === 0) return repoErrorPayload('name', `Repository "${name}" not found`)
      return { repository: mapRepository(row.rows[0]), errors: [] }
    }

    values.push(name)
    try {
      const result = await ctx.pool.query<Record<string, unknown>>(
        `UPDATE repositories SET ${setClauses.join(', ')} WHERE name = $${values.length} RETURNING *`,
        values
      )
      if (result.rows.length === 0) return repoErrorPayload('name', `Repository "${name}" not found`)
      return { repository: mapRepository(result.rows[0]), errors: [] }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update repository'
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
