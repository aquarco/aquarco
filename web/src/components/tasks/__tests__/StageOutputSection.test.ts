/**
 * Tests for StageOutputSection display logic (GitHub issue #139).
 *
 * Validates the three UX improvements:
 * 1. Run suffix — "(2nd run)", "(3rd run)", etc. instead of "(next run)"
 * 2. Stage name resolution — pipeline definition names instead of raw categories
 * 3. Run counting — correct per-stageNumber run tracking
 *
 * These tests extract the pure logic from StageOutputSection.tsx and validate
 * it without requiring a React/Apollo render context.
 */

import { describe, it, expect } from 'vitest'
import type { PipelineStageDefn } from '@/app/tasks/[id]/types'

// ── Extracted logic from StageOutputSection.tsx ─────────────────────────

/**
 * Computes the run suffix label for a stage occurrence.
 * Mirrors lines 53-56 of StageOutputSection.tsx.
 */
function getRunSuffix(runCount: number): string {
  if (runCount === 2) return ' (2nd run)'
  if (runCount === 3) return ' (3rd run)'
  if (runCount > 3) return ` (${runCount}th run)`
  return ''
}

/**
 * Resolves the display name for a stage.
 * Mirrors line 58 of StageOutputSection.tsx.
 */
function getStageName(
  stageNumber: number,
  category: string,
  defnStages: PipelineStageDefn[],
): string {
  return defnStages[stageNumber]?.name ?? category.toUpperCase()
}

/**
 * Computes run counts per stageNumber across a list of stages.
 * Mirrors lines 45-51 of StageOutputSection.tsx.
 */
function computeRunCounts(stages: Array<{ stageNumber: number }>): Map<number, number[]> {
  const runCountPerStageNumber = new Map<number, number>()
  const result = new Map<number, number[]>()

  for (const stage of stages) {
    const stageNum = stage.stageNumber
    const runCount = (runCountPerStageNumber.get(stageNum) ?? 0) + 1
    runCountPerStageNumber.set(stageNum, runCount)

    if (!result.has(stageNum)) result.set(stageNum, [])
    result.get(stageNum)!.push(runCount)
  }

  return result
}

/**
 * Determines the effective status for display purposes.
 * Mirrors lines 60-61 of StageOutputSection.tsx.
 */
function getEffectiveStatus(
  status: string,
  stageNumber: number,
  effectiveExecutingStages: Set<number>,
): string {
  return status === 'PENDING' && effectiveExecutingStages.has(stageNumber)
    ? 'EXECUTING'
    : status
}

// ── Tests ───────────────────────────────────────────────────────────────

describe('StageOutputSection — run suffix (issue #139 item 1)', () => {
  it('returns empty string for 1st run', () => {
    expect(getRunSuffix(1)).toBe('')
  })

  it('returns "(2nd run)" for 2nd occurrence', () => {
    expect(getRunSuffix(2)).toBe(' (2nd run)')
  })

  it('returns "(3rd run)" for 3rd occurrence', () => {
    expect(getRunSuffix(3)).toBe(' (3rd run)')
  })

  it('returns "(4th run)" for 4th occurrence', () => {
    expect(getRunSuffix(4)).toBe(' (4th run)')
  })

  it('returns "(5th run)" for 5th occurrence', () => {
    expect(getRunSuffix(5)).toBe(' (5th run)')
  })

  it('returns "(10th run)" for 10th occurrence', () => {
    expect(getRunSuffix(10)).toBe(' (10th run)')
  })

  it('returns empty string for 0 (edge case)', () => {
    expect(getRunSuffix(0)).toBe('')
  })

  it('returns empty string for negative (edge case)', () => {
    expect(getRunSuffix(-1)).toBe('')
  })
})

describe('StageOutputSection — stage name resolution (issue #139 item 2)', () => {
  const defnStages: PipelineStageDefn[] = [
    { name: 'ANALYZE', category: 'analyze', required: true, conditions: [] },
    { name: 'DESIGN', category: 'design', required: true, conditions: [] },
    { name: 'IMPLEMENT', category: 'implement', required: true, conditions: [] },
    { name: 'REVIEW', category: 'review', required: true, conditions: [] },
    { name: 'FIX REVIEW FINDINGS', category: 'implement', required: false, conditions: [] },
    { name: 'TEST', category: 'test', required: true, conditions: [] },
    { name: 'DOCUMENT', category: 'document', required: false, conditions: [] },
  ]

  it('resolves stage name from pipeline definition by index', () => {
    expect(getStageName(0, 'analyze', defnStages)).toBe('ANALYZE')
    expect(getStageName(1, 'design', defnStages)).toBe('DESIGN')
    expect(getStageName(2, 'implement', defnStages)).toBe('IMPLEMENT')
  })

  it('shows FIX REVIEW FINDINGS for stage 4 instead of raw IMPLEMENT', () => {
    expect(getStageName(4, 'implement', defnStages)).toBe('FIX REVIEW FINDINGS')
  })

  it('falls back to category.toUpperCase() when stageNumber exceeds defn length', () => {
    expect(getStageName(10, 'implement', defnStages)).toBe('IMPLEMENT')
  })

  it('falls back to category.toUpperCase() when defnStages is empty', () => {
    expect(getStageName(0, 'analyze', [])).toBe('ANALYZE')
  })

  it('handles system categories with uppercase fallback', () => {
    expect(getStageName(99, 'condition-eval', defnStages)).toBe('CONDITION-EVAL')
  })

  it('handles planning category with uppercase fallback', () => {
    expect(getStageName(99, 'planning', defnStages)).toBe('PLANNING')
  })
})

describe('StageOutputSection — run count tracking', () => {
  it('tracks single run per stage', () => {
    const stages = [
      { stageNumber: 0 },
      { stageNumber: 1 },
      { stageNumber: 2 },
    ]
    const counts = computeRunCounts(stages)
    expect(counts.get(0)).toEqual([1])
    expect(counts.get(1)).toEqual([1])
    expect(counts.get(2)).toEqual([1])
  })

  it('tracks multiple runs for same stageNumber', () => {
    const stages = [
      { stageNumber: 0 },
      { stageNumber: 1 },
      { stageNumber: 1 },
      { stageNumber: 1 },
    ]
    const counts = computeRunCounts(stages)
    expect(counts.get(0)).toEqual([1])
    expect(counts.get(1)).toEqual([1, 2, 3])
  })

  it('handles interleaved stage numbers (implement-review-implement pattern)', () => {
    const stages = [
      { stageNumber: 0 },  // analyze
      { stageNumber: 1 },  // design
      { stageNumber: 2 },  // implement (1st)
      { stageNumber: 3 },  // review
      { stageNumber: 2 },  // implement (2nd — fix review findings)
      { stageNumber: 2 },  // implement (3rd)
    ]
    const counts = computeRunCounts(stages)
    expect(counts.get(2)).toEqual([1, 2, 3])
    expect(counts.get(3)).toEqual([1])
  })

  it('handles empty stages array', () => {
    const counts = computeRunCounts([])
    expect(counts.size).toBe(0)
  })

  it('handles five runs of same stage (maxRepeats scenario)', () => {
    const stages = Array.from({ length: 5 }, () => ({ stageNumber: 2 }))
    const counts = computeRunCounts(stages)
    expect(counts.get(2)).toEqual([1, 2, 3, 4, 5])
  })
})

describe('StageOutputSection — effective status override', () => {
  it('overrides PENDING to EXECUTING when stage is in effectiveExecutingStages', () => {
    const result = getEffectiveStatus('PENDING', 2, new Set([2]))
    expect(result).toBe('EXECUTING')
  })

  it('keeps PENDING when stage is NOT in effectiveExecutingStages', () => {
    const result = getEffectiveStatus('PENDING', 2, new Set([3]))
    expect(result).toBe('PENDING')
  })

  it('does not override non-PENDING statuses', () => {
    expect(getEffectiveStatus('COMPLETED', 2, new Set([2]))).toBe('COMPLETED')
    expect(getEffectiveStatus('FAILED', 2, new Set([2]))).toBe('FAILED')
    expect(getEffectiveStatus('EXECUTING', 2, new Set([2]))).toBe('EXECUTING')
  })

  it('works with empty effectiveExecutingStages set', () => {
    expect(getEffectiveStatus('PENDING', 0, new Set())).toBe('PENDING')
  })
})

describe('StageOutputSection — token total computation', () => {
  /**
   * Mirrors line 64 of StageOutputSection.tsx:
   *   (stage.tokensInput ?? 0) + (stage.tokensOutput ?? 0) +
   *   (stage.cacheReadTokens ?? 0) + (stage.cacheWriteTokens ?? 0)
   */
  function computeStageTotalTokens(stage: {
    tokensInput: number | null
    tokensOutput: number | null
    cacheReadTokens: number | null
    cacheWriteTokens: number | null
  }): number {
    return (
      (stage.tokensInput ?? 0) +
      (stage.tokensOutput ?? 0) +
      (stage.cacheReadTokens ?? 0) +
      (stage.cacheWriteTokens ?? 0)
    )
  }

  it('sums all four token fields', () => {
    const total = computeStageTotalTokens({
      tokensInput: 1000,
      tokensOutput: 500,
      cacheReadTokens: 200,
      cacheWriteTokens: 100,
    })
    expect(total).toBe(1800)
  })

  it('treats null fields as zero', () => {
    const total = computeStageTotalTokens({
      tokensInput: 1000,
      tokensOutput: null,
      cacheReadTokens: null,
      cacheWriteTokens: null,
    })
    expect(total).toBe(1000)
  })

  it('returns zero when all fields are null', () => {
    const total = computeStageTotalTokens({
      tokensInput: null,
      tokensOutput: null,
      cacheReadTokens: null,
      cacheWriteTokens: null,
    })
    expect(total).toBe(0)
  })
})

describe('StageOutputSection — combined display label', () => {
  const defnStages: PipelineStageDefn[] = [
    { name: 'ANALYZE', category: 'analyze', required: true, conditions: [] },
    { name: 'DESIGN', category: 'design', required: true, conditions: [] },
    { name: 'IMPLEMENT', category: 'implement', required: true, conditions: [] },
    { name: 'REVIEW', category: 'review', required: true, conditions: [] },
    { name: 'FIX REVIEW FINDINGS', category: 'implement', required: false, conditions: [] },
    { name: 'TEST', category: 'test', required: true, conditions: [] },
  ]

  function getDisplayLabel(
    stageNumber: number,
    category: string,
    runCount: number,
    pipelineDefnStages: PipelineStageDefn[],
  ): string {
    const stageName = getStageName(stageNumber, category, pipelineDefnStages)
    const runSuffix = getRunSuffix(runCount)
    return `${stageName}${runSuffix}`
  }

  it('shows "IMPLEMENT" for first implement run', () => {
    expect(getDisplayLabel(2, 'implement', 1, defnStages)).toBe('IMPLEMENT')
  })

  it('shows "FIX REVIEW FINDINGS" for the re-implement stage', () => {
    expect(getDisplayLabel(4, 'implement', 1, defnStages)).toBe('FIX REVIEW FINDINGS')
  })

  it('shows "IMPLEMENT (2nd run)" for second implement at stage 2', () => {
    expect(getDisplayLabel(2, 'implement', 2, defnStages)).toBe('IMPLEMENT (2nd run)')
  })

  it('shows "FIX REVIEW FINDINGS (2nd run)" for repeated fix stage', () => {
    expect(getDisplayLabel(4, 'implement', 2, defnStages)).toBe('FIX REVIEW FINDINGS (2nd run)')
  })

  it('shows "TEST (3rd run)" for triple test run', () => {
    expect(getDisplayLabel(5, 'test', 3, defnStages)).toBe('TEST (3rd run)')
  })

  it('falls back to uppercase category when pipeline defn unavailable', () => {
    expect(getDisplayLabel(0, 'analyze', 1, [])).toBe('ANALYZE')
  })
})
