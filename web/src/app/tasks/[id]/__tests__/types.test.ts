/**
 * Tests for web/src/app/tasks/[id]/types.ts — type definitions
 * extracted during the codebase simplification refactoring (#109).
 *
 * These are compile-time type checks: if they compile, the types are correct.
 * We also verify the shape at runtime for key interfaces.
 */

import { describe, it, expect } from 'vitest'
import type { Stage, Task, ContextEntry, PipelineCondition, PipelineStageDefn } from '../types'

describe('Stage type', () => {
  it('accepts a complete Stage object with model and rawOutput', () => {
    const stage: Stage = {
      id: '1',
      stageNumber: 0,
      iteration: 1,
      run: 1,
      executionOrder: 1,
      category: 'analyze',
      agent: 'analyze-agent',
      agentVersion: '1.0.0',
      status: 'completed',
      startedAt: '2026-04-08T00:00:00Z',
      completedAt: '2026-04-08T00:01:00Z',
      structuredOutput: { summary: 'done' },
      tokensInput: 100,
      tokensOutput: 50,
      costUsd: 0.01,
      cacheReadTokens: 10,
      cacheWriteTokens: 5,
      model: 'claude-sonnet-4-20250514',
      rawOutput: '{"type":"result"}',
      errorMessage: null,
      retryCount: 0,
      liveOutput: null,
    }
    expect(stage.model).toBe('claude-sonnet-4-20250514')
    expect(stage.rawOutput).toBe('{"type":"result"}')
  })

  it('allows model and rawOutput to be null', () => {
    const stage: Stage = {
      id: '2',
      stageNumber: 1,
      iteration: 1,
      run: 1,
      executionOrder: null,
      category: 'implement',
      agent: null,
      agentVersion: null,
      status: 'pending',
      startedAt: null,
      completedAt: null,
      structuredOutput: null,
      tokensInput: null,
      tokensOutput: null,
      costUsd: null,
      cacheReadTokens: null,
      cacheWriteTokens: null,
      model: null,
      rawOutput: null,
      errorMessage: null,
      retryCount: 0,
      liveOutput: null,
    }
    expect(stage.model).toBeNull()
    expect(stage.rawOutput).toBeNull()
  })
})

describe('ContextEntry type', () => {
  it('has expected shape', () => {
    const entry: ContextEntry = {
      id: 'ctx-1',
      key: 'design-doc',
      valueType: 'text',
      valueJson: null,
      valueText: 'Some design doc content',
      valueFileRef: null,
      createdAt: '2026-04-08T00:00:00Z',
      stageNumber: 1,
    }
    expect(entry.key).toBe('design-doc')
  })
})

describe('PipelineCondition type', () => {
  it('has expected shape', () => {
    const cond: PipelineCondition = {
      type: 'simple',
      expression: 'tests_passed > 0',
      onYes: null,
      onNo: 'implement',
      maxRepeats: 3,
    }
    expect(cond.type).toBe('simple')
    expect(cond.maxRepeats).toBe(3)
  })
})

describe('PipelineStageDefn type', () => {
  it('has expected shape', () => {
    const defn: PipelineStageDefn = {
      name: 'Test Stage',
      category: 'test',
      required: true,
      conditions: [],
    }
    expect(defn.category).toBe('test')
  })
})
