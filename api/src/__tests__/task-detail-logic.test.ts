/**
 * Tests for pure logic functions used in the task detail page
 * (web/src/app/tasks/[id]/page.tsx).
 *
 * Because the page is a React component with Apollo and MUI dependencies,
 * we extract and test the logic here as pure TypeScript — no rendering needed.
 *
 * Covers:
 *   - Stage sort order (chronological by stageNumber → iteration → run)
 *   - SVG deduplication via Map with last-write-wins semantics
 *   - activeStep calculation (index of first non-completed/non-skipped stage)
 *   - Run label suffix generation (first run / next run / 3rd run / Nth run)
 *   - resolveContextValue: priority order (json > text > fileRef > dash)
 *
 * NOT covered:
 *   - React rendering and MUI component behavior (requires jsdom + RTL setup
 *     not present in the web package; the page renders no plain HTML testable nodes)
 *   - Apollo query/subscription hooks (integration-level concern)
 *   - Playwright e2e flows (delegated to the e2e agent)
 */

import { describe, it, expect } from '@jest/globals'

// ── Types mirrored from page.tsx ──────────────────────────────────────────────

interface Stage {
  id: string
  stageNumber: number
  iteration: number
  run: number
  category: string
  agent: string | null
  agentVersion: string | null
  status: string
  startedAt: string | null
  completedAt: string | null
  structuredOutput: unknown | null
  rawOutput: string | null
  tokensInput: number | null
  tokensOutput: number | null
  errorMessage: string | null
  retryCount: number
  liveOutput: string | null
}

interface ContextEntry {
  id: string
  key: string
  valueType: string
  valueJson: unknown | null
  valueText: string | null
  valueFileRef: string | null
  createdAt: string
  stageNumber: number | null
}

// ── Logic extracted verbatim from page.tsx ────────────────────────────────────

/**
 * Sort all stage runs in chronological order.
 * Mirrors the sort in TaskDetailPage:
 *   stages.slice().sort((a, b) => { ... })
 */
function sortStages(stages: Stage[]): Stage[] {
  return stages.slice().sort((a, b) => {
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })
}

/**
 * Deduplicate stages for SVG diagram: one entry per unique stageNumber,
 * last write wins (so latest run status is shown).
 * Mirrors the Map construction in TaskDetailPage.
 */
function deduplicateStagesForSvg(sortedStages: Stage[]): Stage[] {
  const uniqueStagesMap = new Map<number, Stage>()
  for (const s of sortedStages) {
    uniqueStagesMap.set(s.stageNumber, s)
  }
  return Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)
}

/**
 * Find the active step index for the SVG diagram.
 * Mirrors: uniqueStages.findIndex(s => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED'))
 */
function findActiveStep(uniqueStages: Stage[]): number {
  return uniqueStages.findIndex(
    (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
  )
}

/**
 * Build the run label suffix for a given 1-based occurrence count of this stage number.
 * Mirrors the runSuffix logic in the Stage Output section of TaskDetailPage.
 */
function buildRunSuffix(runCount: number): string {
  if (runCount === 1) return ''
  if (runCount === 2) return ' (next run)'
  if (runCount === 3) return ' (3rd run)'
  return ` (${runCount}th run)`
}

/**
 * Resolve the display value and isJson flag for a context entry.
 * Mirrors resolveContextValue in page.tsx exactly.
 */
function resolveContextValue(entry: ContextEntry): { display: string; isJson: boolean } {
  if (entry.valueJson != null) {
    return { display: JSON.stringify(entry.valueJson, null, 2), isJson: true }
  }
  if (entry.valueText != null) {
    return { display: entry.valueText, isJson: false }
  }
  if (entry.valueFileRef != null) {
    return { display: entry.valueFileRef, isJson: false }
  }
  return { display: '—', isJson: false }
}

// ── Stage fixtures ─────────────────────────────────────────────────────────────

function makeStage(overrides: Partial<Stage> & { id: string; stageNumber: number; iteration: number; run: number }): Stage {
  return {
    category: 'ANALYZE',
    agent: null,
    agentVersion: null,
    status: 'COMPLETED',
    startedAt: null,
    completedAt: null,
    structuredOutput: null,
    rawOutput: null,
    tokensInput: null,
    tokensOutput: null,
    errorMessage: null,
    retryCount: 0,
    liveOutput: null,
    ...overrides,
  }
}

// ── sortStages ─────────────────────────────────────────────────────────────────

describe('sortStages', () => {
  it('should sort by stageNumber ascending', () => {
    const stages = [
      makeStage({ id: 's2', stageNumber: 1, iteration: 1, run: 1 }),
      makeStage({ id: 's1', stageNumber: 0, iteration: 1, run: 1 }),
    ]
    const sorted = sortStages(stages)
    expect(sorted[0].id).toBe('s1')
    expect(sorted[1].id).toBe('s2')
  })

  it('should sort by iteration ascending when stageNumber is equal', () => {
    const stages = [
      makeStage({ id: 'iter2', stageNumber: 0, iteration: 2, run: 1 }),
      makeStage({ id: 'iter1', stageNumber: 0, iteration: 1, run: 1 }),
    ]
    const sorted = sortStages(stages)
    expect(sorted[0].id).toBe('iter1')
    expect(sorted[1].id).toBe('iter2')
  })

  it('should sort by run ascending when both stageNumber and iteration are equal', () => {
    const stages = [
      makeStage({ id: 'run3', stageNumber: 0, iteration: 1, run: 3 }),
      makeStage({ id: 'run1', stageNumber: 0, iteration: 1, run: 1 }),
      makeStage({ id: 'run2', stageNumber: 0, iteration: 1, run: 2 }),
    ]
    const sorted = sortStages(stages)
    expect(sorted.map((s) => s.id)).toEqual(['run1', 'run2', 'run3'])
  })

  it('should handle a mixed multi-stage multi-run pipeline in correct order', () => {
    const stages = [
      makeStage({ id: 's1-iter2', stageNumber: 0, iteration: 2, run: 1 }),
      makeStage({ id: 's2-iter1', stageNumber: 1, iteration: 1, run: 1 }),
      makeStage({ id: 's1-iter1', stageNumber: 0, iteration: 1, run: 1 }),
      makeStage({ id: 's0-iter1-run2', stageNumber: 0, iteration: 1, run: 2 }),
    ]
    const sorted = sortStages(stages)
    expect(sorted.map((s) => s.id)).toEqual([
      's1-iter1',       // stageNumber 0, iteration 1, run 1
      's0-iter1-run2',  // stageNumber 0, iteration 1, run 2
      's1-iter2',       // stageNumber 0, iteration 2, run 1
      's2-iter1',       // stageNumber 1, iteration 1, run 1
    ])
  })

  it('should not mutate the original array', () => {
    const stages = [
      makeStage({ id: 'b', stageNumber: 1, iteration: 1, run: 1 }),
      makeStage({ id: 'a', stageNumber: 0, iteration: 1, run: 1 }),
    ]
    const original = stages.slice()
    sortStages(stages)
    expect(stages[0].id).toBe(original[0].id)
    expect(stages[1].id).toBe(original[1].id)
  })

  it('should return empty array when given empty input', () => {
    expect(sortStages([])).toHaveLength(0)
  })
})

// ── deduplicateStagesForSvg ────────────────────────────────────────────────────

describe('deduplicateStagesForSvg — last-write-wins Map deduplication', () => {
  it('should return one stage per unique stageNumber', () => {
    const stages = [
      makeStage({ id: 'run1', stageNumber: 0, iteration: 1, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 'run2', stageNumber: 0, iteration: 1, run: 2, status: 'FAILED' }),
    ]
    const result = deduplicateStagesForSvg(stages)
    expect(result).toHaveLength(1)
  })

  it('should keep the LAST entry for a given stageNumber (latest run wins)', () => {
    const stages = [
      makeStage({ id: 'run1', stageNumber: 0, iteration: 1, run: 1, status: 'FAILED' }),
      makeStage({ id: 'run2', stageNumber: 0, iteration: 1, run: 2, status: 'COMPLETED' }),
    ]
    const result = deduplicateStagesForSvg(stages)
    // Input is already sorted chronologically; last write = run2
    expect(result[0].id).toBe('run2')
    expect(result[0].status).toBe('COMPLETED')
  })

  it('should preserve all stages when every stageNumber is unique', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1 }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1 }),
      makeStage({ id: 's2', stageNumber: 2, iteration: 1, run: 1 }),
    ]
    const result = deduplicateStagesForSvg(stages)
    expect(result).toHaveLength(3)
  })

  it('should sort the deduplicated result by stageNumber ascending', () => {
    // Provide already-sorted input; verify output order is preserved
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1 }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1 }),
    ]
    const result = deduplicateStagesForSvg(stages)
    expect(result[0].stageNumber).toBe(0)
    expect(result[1].stageNumber).toBe(1)
  })

  it('should return empty array when given empty input', () => {
    expect(deduplicateStagesForSvg([])).toHaveLength(0)
  })

  it('should handle a pipeline with multiple multi-run stages', () => {
    // 3 runs of stage 0, 2 runs of stage 1, 1 run of stage 2 => 3 deduplicated
    const stages = [
      makeStage({ id: 's0-r1', stageNumber: 0, iteration: 1, run: 1, status: 'FAILED' }),
      makeStage({ id: 's0-r2', stageNumber: 0, iteration: 1, run: 2, status: 'FAILED' }),
      makeStage({ id: 's0-r3', stageNumber: 0, iteration: 2, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 's1-r1', stageNumber: 1, iteration: 1, run: 1, status: 'FAILED' }),
      makeStage({ id: 's1-r2', stageNumber: 1, iteration: 1, run: 2, status: 'COMPLETED' }),
      makeStage({ id: 's2-r1', stageNumber: 2, iteration: 1, run: 1, status: 'EXECUTING' }),
    ]
    const result = deduplicateStagesForSvg(stages)
    expect(result).toHaveLength(3)
    // Latest run for stage 0 is 's0-r3' (COMPLETED)
    expect(result[0].id).toBe('s0-r3')
    // Latest run for stage 1 is 's1-r2' (COMPLETED)
    expect(result[1].id).toBe('s1-r2')
    // Only run for stage 2 is 's2-r1' (EXECUTING)
    expect(result[2].id).toBe('s2-r1')
  })
})

// ── findActiveStep ─────────────────────────────────────────────────────────────

describe('findActiveStep', () => {
  it('should return index of the EXECUTING stage', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1, status: 'EXECUTING' }),
      makeStage({ id: 's2', stageNumber: 2, iteration: 1, run: 1, status: 'PENDING' }),
    ]
    expect(findActiveStep(stages)).toBe(1)
  })

  it('should return 0 when first stage is PENDING', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1, status: 'PENDING' }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1, status: 'PENDING' }),
    ]
    expect(findActiveStep(stages)).toBe(0)
  })

  it('should return -1 when all stages are COMPLETED', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1, status: 'COMPLETED' }),
    ]
    expect(findActiveStep(stages)).toBe(-1)
  })

  it('should skip SKIPPED stages when looking for active step', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1, status: 'SKIPPED' }),
      makeStage({ id: 's2', stageNumber: 2, iteration: 1, run: 1, status: 'PENDING' }),
    ]
    // Stage 1 is SKIPPED so it is not active; stage 2 is PENDING, not COMPLETED or SKIPPED
    expect(findActiveStep(stages)).toBe(2)
  })

  it('should return -1 when all stages are COMPLETED or SKIPPED', () => {
    const stages = [
      makeStage({ id: 's0', stageNumber: 0, iteration: 1, run: 1, status: 'COMPLETED' }),
      makeStage({ id: 's1', stageNumber: 1, iteration: 1, run: 1, status: 'SKIPPED' }),
    ]
    expect(findActiveStep(stages)).toBe(-1)
  })

  it('should return -1 for empty stages array', () => {
    expect(findActiveStep([])).toBe(-1)
  })
})

// ── buildRunSuffix ─────────────────────────────────────────────────────────────

describe('buildRunSuffix', () => {
  it('should return empty string for the first occurrence (runCount=1)', () => {
    expect(buildRunSuffix(1)).toBe('')
  })

  it('should return " (next run)" for the second occurrence (runCount=2)', () => {
    expect(buildRunSuffix(2)).toBe(' (next run)')
  })

  it('should return " (3rd run)" for the third occurrence (runCount=3)', () => {
    expect(buildRunSuffix(3)).toBe(' (3rd run)')
  })

  it('should return " (4th run)" for the fourth occurrence (runCount=4)', () => {
    expect(buildRunSuffix(4)).toBe(' (4th run)')
  })

  it('should return " (5th run)" for the fifth occurrence (runCount=5)', () => {
    expect(buildRunSuffix(5)).toBe(' (5th run)')
  })

  it('should return " (10th run)" for ten occurrences (runCount=10)', () => {
    expect(buildRunSuffix(10)).toBe(' (10th run)')
  })

  it('should include the count in the suffix for any runCount > 3', () => {
    for (let n = 4; n <= 20; n++) {
      expect(buildRunSuffix(n)).toBe(` (${n}th run)`)
    }
  })

  it('should correctly accumulate run counts across a flat stage list', () => {
    // Simulates the runCountPerStageNumber counter from the page's render loop
    const stages = [
      makeStage({ id: 's0-r1', stageNumber: 0, iteration: 1, run: 1 }),
      makeStage({ id: 's0-r2', stageNumber: 0, iteration: 1, run: 2 }),
      makeStage({ id: 's0-r3', stageNumber: 0, iteration: 2, run: 1 }),
      makeStage({ id: 's1-r1', stageNumber: 1, iteration: 1, run: 1 }),
      makeStage({ id: 's1-r2', stageNumber: 1, iteration: 1, run: 2 }),
    ]

    const runCountPerStageNumber = new Map<number, number>()
    const suffixes: string[] = []
    for (const stage of stages) {
      const stageNum = stage.stageNumber
      const runCount = (runCountPerStageNumber.get(stageNum) ?? 0) + 1
      runCountPerStageNumber.set(stageNum, runCount)
      suffixes.push(buildRunSuffix(runCount))
    }

    expect(suffixes).toEqual([
      '',              // stage 0, 1st occurrence
      ' (next run)',   // stage 0, 2nd occurrence
      ' (3rd run)',    // stage 0, 3rd occurrence
      '',              // stage 1, 1st occurrence
      ' (next run)',   // stage 1, 2nd occurrence
    ])
  })
})

// ── resolveContextValue ────────────────────────────────────────────────────────

describe('resolveContextValue', () => {
  const baseEntry: ContextEntry = {
    id: 'ctx-1',
    key: 'myKey',
    valueType: 'json',
    valueJson: null,
    valueText: null,
    valueFileRef: null,
    createdAt: '2026-01-01T00:00:00Z',
    stageNumber: null,
  }

  it('should return JSON-stringified value and isJson=true when valueJson is set', () => {
    const entry = { ...baseEntry, valueJson: { foo: 'bar', count: 42 } }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(true)
    expect(display).toBe(JSON.stringify({ foo: 'bar', count: 42 }, null, 2))
  })

  it('should prefer valueJson over valueText and valueFileRef', () => {
    const entry = {
      ...baseEntry,
      valueJson: { priority: 'json wins' },
      valueText: 'should be ignored',
      valueFileRef: '/also/ignored',
    }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(true)
    expect(display).toContain('json wins')
  })

  it('should return valueText and isJson=false when valueJson is null', () => {
    const entry = { ...baseEntry, valueJson: null, valueText: 'some plain text' }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(false)
    expect(display).toBe('some plain text')
  })

  it('should prefer valueText over valueFileRef', () => {
    const entry = { ...baseEntry, valueJson: null, valueText: 'text wins', valueFileRef: '/ignored' }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(false)
    expect(display).toBe('text wins')
  })

  it('should return valueFileRef and isJson=false when valueJson and valueText are null', () => {
    const entry = { ...baseEntry, valueJson: null, valueText: null, valueFileRef: '/path/to/file.txt' }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(false)
    expect(display).toBe('/path/to/file.txt')
  })

  it('should return em-dash and isJson=false when all value fields are null', () => {
    const entry = { ...baseEntry, valueJson: null, valueText: null, valueFileRef: null }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(false)
    expect(display).toBe('—')
  })

  it('should treat false/zero/empty-string valueJson as non-null (truthy-check guard must not apply)', () => {
    // valueJson = false — not null, so should be treated as JSON
    const entry = { ...baseEntry, valueJson: false }
    const { isJson } = resolveContextValue(entry)
    expect(isJson).toBe(true)
  })

  it('should treat valueJson = 0 as non-null JSON', () => {
    const entry = { ...baseEntry, valueJson: 0 }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(true)
    expect(display).toBe('0')
  })

  it('should handle valueJson being an array', () => {
    const entry = { ...baseEntry, valueJson: [1, 2, 3] }
    const { display, isJson } = resolveContextValue(entry)
    expect(isJson).toBe(true)
    expect(display).toBe(JSON.stringify([1, 2, 3], null, 2))
  })

  it('should handle empty string valueText as a valid display value', () => {
    const entry = { ...baseEntry, valueJson: null, valueText: '' }
    const { display, isJson } = resolveContextValue(entry)
    // '' is not null so valueText branch should fire
    expect(isJson).toBe(false)
    expect(display).toBe('')
  })
})
