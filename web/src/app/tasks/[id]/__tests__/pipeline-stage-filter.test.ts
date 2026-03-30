/**
 * Tests for the SYSTEM_CATEGORIES filtering logic used in the pipeline flow diagram.
 *
 * Validates the fix: "exclude system stages from pipeline flow diagram"
 * Commit: d4cd6f430cd8d979d911c898d6c8d7b5d5b00ac5
 *
 * The logic under test (from page.tsx ~line 480):
 *
 *   const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
 *   const uniqueStagesMap = new Map<number, Stage>()
 *   for (const s of stages) {
 *     if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
 *     uniqueStagesMap.set(s.stageNumber, s)
 *   }
 *   const uniqueStages = Array.from(uniqueStagesMap.values())
 *     .sort((a, b) => a.stageNumber - b.stageNumber)
 */

import { describe, it, expect } from 'vitest'

// ---------------------------------------------------------------------------
// Minimal Stage type mirroring the one in page.tsx
// ---------------------------------------------------------------------------
interface Stage {
  id: string
  stageNumber: number
  iteration?: number
  run?: number
  category: string
  agent: string
  status: string
}

// ---------------------------------------------------------------------------
// The filtering / deduplication function extracted verbatim from page.tsx
// ---------------------------------------------------------------------------

const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])

function buildUniqueStages(rawStages: Stage[]): Stage[] {
  // Sort by stageNumber asc, then iteration asc, then run asc  (mirrors page.tsx ~470)
  const stages = rawStages.slice().sort((a, b) => {
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })

  // Deduplicate: last-write-wins per stageNumber; exclude system categories (page.tsx ~480)
  const uniqueStagesMap = new Map<number, Stage>()
  for (const s of stages) {
    if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
    uniqueStagesMap.set(s.stageNumber, s)
  }

  return Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeStage(
  id: string,
  stageNumber: number,
  category: string,
  overrides: Partial<Stage> = {}
): Stage {
  return {
    id,
    stageNumber,
    iteration: 1,
    run: 1,
    category,
    agent: `${category}-agent`,
    status: 'COMPLETED',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SYSTEM_CATEGORIES set', () => {
  it('contains planning', () => {
    expect(SYSTEM_CATEGORIES.has('planning')).toBe(true)
  })

  it('contains condition-eval', () => {
    expect(SYSTEM_CATEGORIES.has('condition-eval')).toBe(true)
  })

  it('does not contain review', () => {
    expect(SYSTEM_CATEGORIES.has('review')).toBe(false)
  })

  it('does not contain implementation', () => {
    expect(SYSTEM_CATEGORIES.has('implementation')).toBe(false)
  })

  it('does not contain test', () => {
    expect(SYSTEM_CATEGORIES.has('test')).toBe(false)
  })
})

describe('buildUniqueStages — system stage exclusion', () => {
  it('excludes planning stages (stageNumber=-1)', () => {
    const stages = [
      makeStage('plan-1', -1, 'planning'),
      makeStage('review-1', 0, 'review'),
      makeStage('impl-1', 1, 'implementation'),
    ]
    const result = buildUniqueStages(stages)
    expect(result.every(s => s.category !== 'planning')).toBe(true)
    expect(result.map(s => s.id)).toEqual(['review-1', 'impl-1'])
  })

  it('excludes condition-eval stages', () => {
    const stages = [
      makeStage('review-1', 0, 'review'),
      makeStage('cond-0', 0, 'condition-eval'),
      makeStage('impl-1', 1, 'implementation'),
      makeStage('cond-1', 1, 'condition-eval'),
    ]
    const result = buildUniqueStages(stages)
    expect(result.every(s => s.category !== 'condition-eval')).toBe(true)
  })

  it('returns only pipeline stages when both system categories are present', () => {
    const stages = [
      makeStage('plan-1', -1, 'planning'),
      makeStage('review-1', 0, 'review'),
      makeStage('cond-0', 0, 'condition-eval'),
      makeStage('impl-1', 1, 'implementation'),
      makeStage('cond-1', 1, 'condition-eval'),
      makeStage('test-1', 2, 'test'),
    ]
    const result = buildUniqueStages(stages)
    expect(result.map(s => s.category)).toEqual(['review', 'implementation', 'test'])
  })

  it('returns empty array when all stages are system stages', () => {
    const stages = [
      makeStage('plan-1', -1, 'planning'),
      makeStage('cond-0', 0, 'condition-eval'),
    ]
    const result = buildUniqueStages(stages)
    expect(result).toEqual([])
  })

  it('excludes system categories regardless of case (e.g., Planning, CONDITION-EVAL)', () => {
    const stages = [
      makeStage('plan-1', -1, 'Planning'),
      makeStage('review-1', 0, 'review'),
      makeStage('cond-0', 0, 'CONDITION-EVAL'),
      makeStage('impl-1', 1, 'implementation'),
    ]
    const result = buildUniqueStages(stages)
    expect(result.every(s => !SYSTEM_CATEGORIES.has(s.category.toLowerCase()))).toBe(true)
    expect(result.map(s => s.category)).toEqual(['review', 'implementation'])
  })

  it('returns all stages when no system stages are present', () => {
    const stages = [
      makeStage('review-1', 0, 'review'),
      makeStage('impl-1', 1, 'implementation'),
      makeStage('test-1', 2, 'test'),
    ]
    const result = buildUniqueStages(stages)
    expect(result).toHaveLength(3)
  })
})

describe('buildUniqueStages — stageNumber index alignment', () => {
  it('planning stage (stageNumber=-1) does not shift pipeline stage indices', () => {
    // Bug fixed: before fix, planning at stageNumber=-1 would be sorted first, consuming
    // index 0 in uniqueStages, shifting review to index 1, implementation to index 2, etc.
    const stages = [
      makeStage('plan-1', -1, 'planning'),
      makeStage('review-1', 0, 'review'),
      makeStage('impl-1', 1, 'implementation'),
    ]
    const result = buildUniqueStages(stages)
    expect(result[0].stageNumber).toBe(0)   // review at index 0
    expect(result[1].stageNumber).toBe(1)   // implementation at index 1
  })

  it('uniqueStages are sorted by stageNumber ascending', () => {
    const stages = [
      makeStage('test-1', 2, 'test'),
      makeStage('impl-1', 1, 'implementation'),
      makeStage('review-1', 0, 'review'),
    ]
    const result = buildUniqueStages(stages)
    expect(result.map(s => s.stageNumber)).toEqual([0, 1, 2])
  })

  it('pipeline stages retain correct stageNumber values after filtering', () => {
    const stages = [
      makeStage('plan-1', -1, 'planning'),
      makeStage('review-1', 0, 'review'),
      makeStage('cond-0', 0, 'condition-eval'),
      makeStage('impl-1', 1, 'implementation'),
    ]
    const result = buildUniqueStages(stages)
    expect(result[0].stageNumber).toBe(0)
    expect(result[1].stageNumber).toBe(1)
  })
})

describe('buildUniqueStages — deduplication (last-write-wins)', () => {
  it('keeps the last run for a given stageNumber when multiple runs exist', () => {
    // Stages sorted by stageNumber→iteration→run, so run=2 is last written → wins
    const stages = [
      makeStage('review-run1', 0, 'review', { run: 1, status: 'FAILED' }),
      makeStage('review-run2', 0, 'review', { run: 2, status: 'COMPLETED' }),
    ]
    const result = buildUniqueStages(stages)
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('review-run2')
    expect(result[0].status).toBe('COMPLETED')
  })

  it('condition-eval stage does not overwrite its co-located pipeline stage', () => {
    // Bug fixed: condition-eval stages share the same stageNumber as the pipeline stage
    // they gate. Without filtering, the map entry for stageNumber=0 would be overwritten
    // by the condition-eval stage (since it appears later in the sorted order), causing
    // wrong status/agent to show in the diagram.
    const stages = [
      makeStage('review-1', 0, 'review', { status: 'COMPLETED', agent: 'review-agent' }),
      makeStage('cond-0', 0, 'condition-eval', { status: 'COMPLETED', agent: 'condition-agent' }),
    ]
    const result = buildUniqueStages(stages)
    expect(result).toHaveLength(1)
    expect(result[0].category).toBe('review')
    expect(result[0].agent).toBe('review-agent')
  })

  it('keeps latest iteration when a stage is retried', () => {
    const stages = [
      makeStage('impl-iter1', 1, 'implementation', { iteration: 1, status: 'FAILED' }),
      makeStage('impl-iter2', 1, 'implementation', { iteration: 2, status: 'COMPLETED' }),
    ]
    const result = buildUniqueStages(stages)
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('impl-iter2')
  })
})

describe('buildUniqueStages — realistic pipeline scenario', () => {
  it('handles a full pr-review-pipeline stage history correctly', () => {
    // Mirrors the actual task from the ticket:
    // planning (-1), review (0), condition-eval (0), implementation (1), test (2)
    const stages = [
      makeStage('planning-1', -1, 'planning', { agent: 'planner-agent', status: 'COMPLETED' }),
      makeStage('review-1', 0, 'review', { agent: 'review-agent', status: 'COMPLETED' }),
      makeStage('cond-eval-0', 0, 'condition-eval', { agent: 'condition-evaluator', status: 'COMPLETED' }),
      makeStage('impl-1', 1, 'implementation', { agent: 'implementation-agent', status: 'COMPLETED' }),
      makeStage('test-1', 2, 'test', { agent: 'test-agent', status: 'EXECUTING' }),
    ]

    const result = buildUniqueStages(stages)

    // Only pipeline stages, in order
    expect(result.map(s => s.category)).toEqual(['review', 'implementation', 'test'])
    expect(result.map(s => s.stageNumber)).toEqual([0, 1, 2])

    // review stage (not cond-eval) wins for stageNumber=0
    expect(result[0].agent).toBe('review-agent')

    // Correct array length for index-based PipelineStagesFlow rendering
    expect(result).toHaveLength(3)
  })

  it('activeStep reflects the currently executing pipeline stage (not a system stage)', () => {
    const stages = [
      makeStage('planning-1', -1, 'planning', { status: 'COMPLETED' }),
      makeStage('review-1', 0, 'review', { status: 'COMPLETED' }),
      makeStage('cond-eval-0', 0, 'condition-eval', { status: 'COMPLETED' }),
      makeStage('impl-1', 1, 'implementation', { status: 'EXECUTING' }),
    ]

    const uniqueStages = buildUniqueStages(stages)
    const activeStep = uniqueStages.findIndex(
      s => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
    )

    // Implementation is at index 1 in uniqueStages (review=0, implementation=1)
    expect(activeStep).toBe(1)
  })
})
