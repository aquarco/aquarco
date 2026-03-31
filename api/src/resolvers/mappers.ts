/**
 * Shared mapper functions used by both queries.ts and mutations.ts.
 * Converts raw DB rows (snake_case) to GraphQL shapes (camelCase).
 */

/** Map a raw DB task row to the GraphQL Task shape */
export function mapTask(row: Record<string, unknown>) {
  return {
    id: row.id,
    title: row.title,
    status: (row.status as string).toUpperCase(),
    priority: row.priority,
    source: row.source,
    sourceRef: row.source_ref ?? null,
    pipeline: row.pipeline ?? 'feature-pipeline',
    pipelineVersion: row.pipeline_version ?? null,
    _repositoryName: row.repository,
    initialContext: row.initial_context ?? null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    startedAt: row.started_at ?? null,
    completedAt: row.completed_at ?? null,
    lastCompletedStageId: row.last_completed_stage ?? null,
    checkpointData: row.checkpoint_data ?? null,
    retryCount: row.retry_count,
    errorMessage: row.error_message ?? null,
    parentTaskId: row.parent_task_id ?? null,
    prNumber: row.pr_number ?? null,
    branchName: row.branch_name ?? null,
  }
}
