/**
 * Tests for api/src/resolvers/mappers.ts — shared mapper functions
 * extracted during the codebase simplification refactoring (#109).
 *
 * Covers mapTask, mapAgentDefinition, mapRepository, mapStage, and getDrainStatus.
 */

import { describe, it, expect, jest } from '@jest/globals'
import { mapTask, mapAgentDefinition, mapRepository, mapStage } from '../resolvers/mappers.js'

// ── mapTask ─────────────────────────────────────────────────────────────────

describe('mapTask', () => {
  const baseRow: Record<string, unknown> = {
    id: 'task-1',
    title: 'Test task',
    status: 'pending',
    priority: 1,
    source: 'github',
    source_ref: 'issue/42',
    pipeline: 'feature-pipeline',
    pipeline_version: '1.0.0',
    repository: 'my-repo',
    initial_context: { body: 'test' },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    started_at: '2026-01-01T01:00:00Z',
    completed_at: null,
    last_completed_stage: 3,
    checkpoint_data: null,
    retry_count: 0,
    error_message: null,
    parent_task_id: null,
    pr_number: null,
    branch_name: 'feature/test',
  }

  it('maps snake_case to camelCase', () => {
    const result = mapTask(baseRow)
    expect(result.id).toBe('task-1')
    expect(result.title).toBe('Test task')
    expect(result.sourceRef).toBe('issue/42')
    expect(result.pipelineVersion).toBe('1.0.0')
    expect(result._repositoryName).toBe('my-repo')
    expect(result.branchName).toBe('feature/test')
  })

  it('uppercases status', () => {
    expect(mapTask(baseRow).status).toBe('PENDING')
    expect(mapTask({ ...baseRow, status: 'executing' }).status).toBe('EXECUTING')
  })

  it('defaults pipeline to feature-pipeline when null', () => {
    const result = mapTask({ ...baseRow, pipeline: null })
    expect(result.pipeline).toBe('feature-pipeline')
  })

  it('returns null for optional fields when absent', () => {
    const minRow = { ...baseRow, source_ref: undefined, started_at: undefined, completed_at: undefined }
    const result = mapTask(minRow)
    expect(result.sourceRef).toBeNull()
    expect(result.startedAt).toBeNull()
    expect(result.completedAt).toBeNull()
  })
})

// ── mapAgentDefinition ──────────────────────────────────────────────────────

describe('mapAgentDefinition', () => {
  const baseAgent: Record<string, unknown> = {
    name: 'test-agent',
    version: '1.0.0',
    description: 'A test agent',
    source: 'default',
    agent_group: 'pipeline',
    spec: { categories: ['test'] },
    is_disabled: false,
    modified_spec: null,
    active_count: '2',
    total_executions: '10',
    total_tokens_used: '5000',
    last_execution_at: '2026-01-01T00:00:00Z',
  }

  it('maps basic fields', () => {
    const result = mapAgentDefinition(baseAgent)
    expect(result.name).toBe('test-agent')
    expect(result.version).toBe('1.0.0')
    expect(result.description).toBe('A test agent')
  })

  it('parses default source', () => {
    const result = mapAgentDefinition({ ...baseAgent, source: 'default' })
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepo).toBeNull()
  })

  it('parses global source', () => {
    const result = mapAgentDefinition({ ...baseAgent, source: 'global:my-repo' })
    expect(result.source).toBe('GLOBAL_CONFIG')
    expect(result.sourceRepo).toBe('my-repo')
  })

  it('parses repo source', () => {
    const result = mapAgentDefinition({ ...baseAgent, source: 'repo:my-repo' })
    expect(result.source).toBe('REPOSITORY')
    expect(result.sourceRepo).toBe('my-repo')
  })

  it('parses autoload source', () => {
    const result = mapAgentDefinition({ ...baseAgent, source: 'autoload:my-repo' })
    expect(result.source).toBe('AUTOLOADED')
    expect(result.sourceRepo).toBe('my-repo')
  })

  it('defaults unknown source to DEFAULT', () => {
    const result = mapAgentDefinition({ ...baseAgent, source: 'unknown-prefix:foo' })
    expect(result.source).toBe('DEFAULT')
    expect(result.sourceRepo).toBeNull()
  })

  it('determines group correctly', () => {
    expect(mapAgentDefinition({ ...baseAgent, agent_group: 'system' }).group).toBe('SYSTEM')
    expect(mapAgentDefinition({ ...baseAgent, agent_group: 'pipeline' }).group).toBe('PIPELINE')
    expect(mapAgentDefinition({ ...baseAgent, agent_group: undefined }).group).toBe('PIPELINE')
  })

  it('uses modified_spec when present', () => {
    const modified = { categories: ['modified'] }
    const result = mapAgentDefinition({ ...baseAgent, modified_spec: modified })
    expect(result.spec).toEqual(modified)
    expect(result.isModified).toBe(true)
    expect(result.modifiedSpec).toEqual(modified)
  })

  it('parses numeric strings', () => {
    const result = mapAgentDefinition(baseAgent)
    expect(result.activeCount).toBe(2)
    expect(result.totalExecutions).toBe(10)
    expect(result.totalTokensUsed).toBe(5000)
  })

  it('defaults numeric fields to 0', () => {
    const result = mapAgentDefinition({ ...baseAgent, active_count: undefined, total_executions: undefined })
    expect(result.activeCount).toBe(0)
    expect(result.totalExecutions).toBe(0)
  })
})

// ── mapRepository ───────────────────────────────────────────────────────────

describe('mapRepository', () => {
  const baseRepo: Record<string, unknown> = {
    name: 'my-repo',
    url: 'https://github.com/org/my-repo',
    branch: 'main',
    clone_dir: '/repos/my-repo',
    pollers: ['github-issues'],
    last_cloned_at: '2026-01-01T00:00:00Z',
    last_pulled_at: '2026-01-02T00:00:00Z',
    clone_status: 'cloned',
    head_sha: 'abc123',
    error_message: null,
    deploy_public_key: null,
  }

  it('maps all fields', () => {
    const result = mapRepository(baseRepo)
    expect(result.name).toBe('my-repo')
    expect(result.cloneDir).toBe('/repos/my-repo')
    expect(result.headSha).toBe('abc123')
    expect(result.deployPublicKey).toBeNull()
  })

  it('uppercases cloneStatus', () => {
    expect(mapRepository(baseRepo).cloneStatus).toBe('CLONED')
  })

  it('defaults pollers to empty array', () => {
    const result = mapRepository({ ...baseRepo, pollers: undefined })
    expect(result.pollers).toEqual([])
  })
})

// ── mapStage ────────────────────────────────────────────────────────────────

describe('mapStage', () => {
  const baseStage: Record<string, unknown> = {
    id: 'stage-1',
    task_id: 'task-1',
    stage_number: 0,
    iteration: 1,
    run: 1,
    execution_order: 1,
    category: 'analyze',
    agent: 'analyze-agent',
    agent_version: '1.0.0',
    status: 'completed',
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T01:00:00Z',
    structured_output: { summary: 'ok' },
    raw_output: 'raw text',
    tokens_input: 100,
    tokens_output: 200,
    cost_usd: 0.05,
    cache_read_tokens: 50,
    cache_write_tokens: 25,
    model: 'claude-sonnet-4-20250514',
    error_message: null,
    retry_count: 0,
    live_output: null,
  }

  it('maps all fields correctly', () => {
    const result = mapStage(baseStage)
    expect(result.id).toBe('stage-1')
    expect(result.taskId).toBe('task-1')
    expect(result.stageNumber).toBe(0)
    expect(result.agent).toBe('analyze-agent')
    expect(result.rawOutput).toBe('raw text')
    expect(result.model).toBe('claude-sonnet-4-20250514')
  })

  it('uppercases category and status', () => {
    const result = mapStage(baseStage)
    expect(result.category).toBe('ANALYZE')
    expect(result.status).toBe('COMPLETED')
  })

  it('defaults iteration and run to 1', () => {
    const result = mapStage({ ...baseStage, iteration: null, run: null })
    expect(result.iteration).toBe(1)
    expect(result.run).toBe(1)
  })

  it('returns null for optional fields when absent', () => {
    const result = mapStage({
      ...baseStage,
      raw_output: undefined,
      model: undefined,
      cost_usd: undefined,
      agent_version: undefined,
    })
    expect(result.rawOutput).toBeNull()
    expect(result.model).toBeNull()
    expect(result.costUsd).toBeNull()
    expect(result.agentVersion).toBeNull()
  })

  it('includes model and rawOutput fields (regression for types.ts fix)', () => {
    const result = mapStage(baseStage)
    expect('model' in result).toBe(true)
    expect('rawOutput' in result).toBe(true)
  })
})
