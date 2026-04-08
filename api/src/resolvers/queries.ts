/**
 * GraphQL Query assembler and shared mapper functions.
 *
 * Combines domain-specific query resolvers into a single Query object.
 * Individual domains live in task-queries.ts and repo-queries.ts.
 *
 * Also exports shared mapper functions (mapRepository, mapStage, mapAgentDefinition,
 * fetchAgentWithOverrides, getDrainStatus) used by mutations and tests.
 */

import { Pool } from 'pg'
import { Context } from '../context.js'
import { taskQueries } from './task-queries.js'
import { repoQueries } from './repo-queries.js'

export const Query = {
  // Task queries
  ...taskQueries,

  // Repository, agent, auth, pipeline queries
  ...repoQueries,

  // Drain status (cross-domain)
  async drainStatus(_: unknown, __: unknown, ctx: Context) {
    return getDrainStatus(ctx.pool)
  },
}

// ---------------------------------------------------------------------------
// Shared mapper functions (exported for use by mutations and tests)
// ---------------------------------------------------------------------------

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
    iteration: (row.iteration as number | null) ?? 1,
    run: (row.run as number | null) ?? 1,
    executionOrder: (row.execution_order as number | null) ?? null,
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
    costUsd: row.cost_usd ?? null,
    cacheReadTokens: row.cache_read_tokens ?? null,
    cacheWriteTokens: row.cache_write_tokens ?? null,
    model: row.model ?? null,
    errorMessage: row.error_message ?? null,
    retryCount: row.retry_count,
    liveOutput: row.live_output ?? null,
  }
}

// ── Drain status helper ──────────────────────────────────────────────────────

export async function getDrainStatus(pool: Pool) {
  // Single atomic query for consistent point-in-time reads
  const { rows } = await pool.query(`
    SELECT
      (SELECT value FROM supervisor_state WHERE key = 'drain_mode') AS drain_val,
      (SELECT COALESCE(SUM(active_count), 0)::int FROM agent_instances) AS active_agents,
      (SELECT COUNT(*)::int FROM tasks WHERE status IN ('executing', 'queued', 'planning')) AS active_tasks
  `)
  const row = rows[0]
  return {
    enabled: row?.drain_val === 'true',
    activeAgents: row?.active_agents ?? 0,
    activeTasks: row?.active_tasks ?? 0,
  }
}
