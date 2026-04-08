/**
 * Tests for the execution_order-aware stage sort function.
 *
 * Validates the sort logic from page.tsx (~line 652):
 *
 *   const stages = task.stages.slice().sort((a, b) => {
 *     const eoA = a.executionOrder
 *     const eoB = b.executionOrder
 *     if (eoA != null && eoB != null) return eoA - eoB
 *     if (eoA != null && eoB == null) return -1
 *     if (eoA == null && eoB != null) return 1
 *     if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
 *     const iterA = a.iteration ?? 1
 *     const iterB = b.iteration ?? 1
 *     if (iterA !== iterB) return iterA - iterB
 *     return (a.run ?? 1) - (b.run ?? 1)
 *   })
 *
 * Introduced by: feat: Add execution_order to stages (#105)
 */

import { describe, it, expect } from 'vitest'

// ---------------------------------------------------------------------------
// Minimal Stage interface matching the GraphQL type used in page.tsx
// ---------------------------------------------------------------------------
interface Stage {
  id: string
  stageNumber: number
  iteration: number | null
  run: number | null
  executionOrder: number | null
  category: string
  status: string
}

// ---------------------------------------------------------------------------
// Sort function extracted verbatim from page.tsx
// ---------------------------------------------------------------------------
function sortStages(stages: Stage[]): Stage[] {
  return stages.slice().sort((a, b) => {
    const eoA = a.executionOrder
    const eoB = b.executionOrder
    // Both have execution_order — sort by it
    if (eoA != null && eoB != null) return eoA - eoB
    // NULLS LAST: non-null before null
    if (eoA != null && eoB == null) return -1
    if (eoA == null && eoB != null) return 1
    // Both null — legacy fallback
    if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
    const iterA = a.iteration ?? 1
    const iterB = b.iteration ?? 1
    if (iterA !== iterB) return iterA - iterB
    return (a.run ?? 1) - (b.run ?? 1)
  })
}

// ---------------------------------------------------------------------------
// Helper to create stages
// ---------------------------------------------------------------------------
function makeStage(overrides: Partial<Stage> & { id: string }): Stage {
  return {
    stageNumber: 0,
    iteration: 1,
    run: 1,
    executionOrder: null,
    category: 'ANALYZE',
    status: 'COMPLETED',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('sortStages — execution_order aware sort', () => {
  // ── Both have executionOrder ─────────────────────────────────────────────

  describe('both stages have executionOrder', () => {
    it('sorts ascending by executionOrder', () => {
      const stages = [
        makeStage({ id: 'b', executionOrder: 3, stageNumber: 1 }),
        makeStage({ id: 'a', executionOrder: 1, stageNumber: 0 }),
        makeStage({ id: 'c', executionOrder: 2, stageNumber: 0 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['a', 'c', 'b'])
    })

    it('handles stage jumps where stageNumber order differs from executionOrder', () => {
      // Pipeline ran: stage 0 → stage 1 → stage 0 (jump back) → stage 2
      const stages = [
        makeStage({ id: 's0-first', stageNumber: 0, executionOrder: 1 }),
        makeStage({ id: 's1', stageNumber: 1, executionOrder: 2 }),
        makeStage({ id: 's0-second', stageNumber: 0, executionOrder: 3 }),
        makeStage({ id: 's2', stageNumber: 2, executionOrder: 4 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual([
        's0-first', 's1', 's0-second', 's2',
      ])
    })

    it('is stable for equal executionOrder (should not occur, but safe)', () => {
      const stages = [
        makeStage({ id: 'x', executionOrder: 1, stageNumber: 0 }),
        makeStage({ id: 'y', executionOrder: 1, stageNumber: 1 }),
      ]
      const sorted = sortStages(stages)
      // Both have eoA - eoB = 0, so order is preserved (stable sort)
      expect(sorted.map((s) => s.id)).toEqual(['x', 'y'])
    })
  })

  // ── NULLS LAST ──────────────────────────────────────────────────────────

  describe('NULLS LAST: non-null executionOrder sorts before null', () => {
    it('stage with executionOrder sorts before stage with null', () => {
      const stages = [
        makeStage({ id: 'pending', executionOrder: null, stageNumber: 0 }),
        makeStage({ id: 'executed', executionOrder: 1, stageNumber: 1 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['executed', 'pending'])
    })

    it('multiple non-null before multiple null', () => {
      const stages = [
        makeStage({ id: 'p1', executionOrder: null, stageNumber: 3 }),
        makeStage({ id: 'p2', executionOrder: null, stageNumber: 4 }),
        makeStage({ id: 'e2', executionOrder: 2, stageNumber: 1 }),
        makeStage({ id: 'e1', executionOrder: 1, stageNumber: 0 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['e1', 'e2', 'p1', 'p2'])
    })

    it('executionOrder=0 is non-null and sorts before null', () => {
      const stages = [
        makeStage({ id: 'null-eo', executionOrder: null, stageNumber: 0 }),
        makeStage({ id: 'zero-eo', executionOrder: 0, stageNumber: 1 }),
      ]
      const sorted = sortStages(stages)
      // 0 != null, so zero-eo should be first
      expect(sorted.map((s) => s.id)).toEqual(['zero-eo', 'null-eo'])
    })
  })

  // ── Legacy fallback (both null) ─────────────────────────────────────────

  describe('legacy fallback when both executionOrder are null', () => {
    it('sorts by stageNumber ASC', () => {
      const stages = [
        makeStage({ id: 's2', executionOrder: null, stageNumber: 2 }),
        makeStage({ id: 's0', executionOrder: null, stageNumber: 0 }),
        makeStage({ id: 's1', executionOrder: null, stageNumber: 1 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['s0', 's1', 's2'])
    })

    it('sorts by iteration ASC when stageNumber is equal', () => {
      const stages = [
        makeStage({ id: 'i3', executionOrder: null, stageNumber: 0, iteration: 3 }),
        makeStage({ id: 'i1', executionOrder: null, stageNumber: 0, iteration: 1 }),
        makeStage({ id: 'i2', executionOrder: null, stageNumber: 0, iteration: 2 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['i1', 'i2', 'i3'])
    })

    it('sorts by run ASC when stageNumber and iteration are equal', () => {
      const stages = [
        makeStage({ id: 'r2', executionOrder: null, stageNumber: 0, iteration: 1, run: 2 }),
        makeStage({ id: 'r1', executionOrder: null, stageNumber: 0, iteration: 1, run: 1 }),
        makeStage({ id: 'r3', executionOrder: null, stageNumber: 0, iteration: 1, run: 3 }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual(['r1', 'r2', 'r3'])
    })

    it('defaults null iteration to 1 and null run to 1', () => {
      const stages = [
        makeStage({ id: 'explicit', executionOrder: null, stageNumber: 0, iteration: 1, run: 1 }),
        makeStage({ id: 'nulls', executionOrder: null, stageNumber: 0, iteration: null, run: null }),
      ]
      const sorted = sortStages(stages)
      // Both effectively have (0, 1, 1) — stable sort preserves input order
      expect(sorted.map((s) => s.id)).toEqual(['explicit', 'nulls'])
    })
  })

  // ── Mixed scenarios ─────────────────────────────────────────────────────

  describe('mixed: real-world pipeline execution with jumps and pending stages', () => {
    it('sorts a realistic pipeline with EO, pending, and system stages', () => {
      // Pipeline: analyze(eo=1) → design(eo=2) → impl(eo=3) → cond-eval(eo=4)
      //   → impl(eo=5, jump back) → test(eo=6) → review(pending, null EO)
      const stages = [
        makeStage({ id: 'review', stageNumber: 4, executionOrder: null, category: 'REVIEW', status: 'PENDING' }),
        makeStage({ id: 'impl-2', stageNumber: 2, executionOrder: 5, iteration: 2, category: 'IMPLEMENT' }),
        makeStage({ id: 'analyze', stageNumber: 0, executionOrder: 1, category: 'ANALYZE' }),
        makeStage({ id: 'cond', stageNumber: 2, executionOrder: 4, category: 'CONDITION-EVAL' }),
        makeStage({ id: 'design', stageNumber: 1, executionOrder: 2, category: 'DESIGN' }),
        makeStage({ id: 'test', stageNumber: 3, executionOrder: 6, category: 'TEST' }),
        makeStage({ id: 'impl-1', stageNumber: 2, executionOrder: 3, category: 'IMPLEMENT' }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual([
        'analyze', 'design', 'impl-1', 'cond', 'impl-2', 'test', 'review',
      ])
    })

    it('handles empty array', () => {
      expect(sortStages([])).toEqual([])
    })

    it('handles single stage', () => {
      const stages = [makeStage({ id: 'only', executionOrder: 1 })]
      const sorted = sortStages(stages)
      expect(sorted).toHaveLength(1)
      expect(sorted[0].id).toBe('only')
    })

    it('does not mutate the input array', () => {
      const stages = [
        makeStage({ id: 'b', executionOrder: 2 }),
        makeStage({ id: 'a', executionOrder: 1 }),
      ]
      const original = [...stages]
      sortStages(stages)
      expect(stages.map((s) => s.id)).toEqual(original.map((s) => s.id))
    })
  })

  // ── Backward compatibility ──────────────────────────────────────────────

  describe('backward compatibility: all null executionOrder (historical data)', () => {
    it('sorts purely by (stageNumber, iteration, run) when no EO is present', () => {
      const stages = [
        makeStage({ id: '0-1-1', stageNumber: 0, iteration: 1, run: 1, executionOrder: null }),
        makeStage({ id: '1-1-1', stageNumber: 1, iteration: 1, run: 1, executionOrder: null }),
        makeStage({ id: '0-1-2', stageNumber: 0, iteration: 1, run: 2, executionOrder: null }),
        makeStage({ id: '0-2-1', stageNumber: 0, iteration: 2, run: 1, executionOrder: null }),
        makeStage({ id: '2-1-1', stageNumber: 2, iteration: 1, run: 1, executionOrder: null }),
      ]
      const sorted = sortStages(stages)
      expect(sorted.map((s) => s.id)).toEqual([
        '0-1-1', '0-1-2', '0-2-1', '1-1-1', '2-1-1',
      ])
    })
  })
})
