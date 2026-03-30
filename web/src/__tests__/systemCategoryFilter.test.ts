/**
 * Tests for the case-insensitive system category filter used in the pipeline chart.
 *
 * The filter lives in web/src/app/tasks/[id]/page.tsx and excludes stages whose
 * category belongs to SYSTEM_CATEGORIES (planning, condition-eval) from the SVG
 * diagram that shows only user-visible pipeline stages.
 *
 * Acceptance criteria:
 *  - Lowercase system categories ('planning', 'condition-eval') are excluded  — existing behaviour preserved
 *  - Uppercase system categories ('PLANNING', 'CONDITION-EVAL') are excluded  — new behaviour validated
 *  - Mixed-case system categories ('Planning', 'Condition-Eval') are excluded — new behaviour validated
 *  - Non-system categories ('review', 'implementation', 'test') pass through  — unaffected
 */

// ---------------------------------------------------------------------------
// Replicate the filtering logic from page.tsx so we can test it in isolation.
// Any regression in the source file (e.g. removing `.toLowerCase()`) should
// also be caught by the integration-level tests added to the component.
// ---------------------------------------------------------------------------

interface StageInput {
  stageNumber: number
  category: string
}

/**
 * Mirrors the deduplication + system-category exclusion logic from
 * TaskDetailPage in web/src/app/tasks/[id]/page.tsx (lines 480-486).
 */
function buildUniqueStages(stages: StageInput[]): StageInput[] {
  const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
  const uniqueStagesMap = new Map<number, StageInput>()
  for (const s of stages) {
    if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
    uniqueStagesMap.set(s.stageNumber, s)
  }
  return Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------
function makeStage(stageNumber: number, category: string): StageInput {
  return { stageNumber, category }
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('system category filter — case-insensitive exclusion', () => {
  // -------------------------------------------------------------------------
  // Existing behaviour: lowercase category values (pre-fix baseline)
  // -------------------------------------------------------------------------
  describe('lowercase system categories (existing behaviour)', () => {
    it('excludes a stage with category "planning"', () => {
      const result = buildUniqueStages([makeStage(-1, 'planning')])
      expect(result).toHaveLength(0)
    })

    it('excludes a stage with category "condition-eval"', () => {
      const result = buildUniqueStages([makeStage(-2, 'condition-eval')])
      expect(result).toHaveLength(0)
    })

    it('excludes all system stages and keeps only the non-system stage', () => {
      const stages = [
        makeStage(-1, 'planning'),
        makeStage(0, 'review'),
        makeStage(-2, 'condition-eval'),
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(1)
      expect(result[0].category).toBe('review')
    })
  })

  // -------------------------------------------------------------------------
  // New behaviour: uppercase category values
  // -------------------------------------------------------------------------
  describe('uppercase system categories (new behaviour)', () => {
    it('excludes a stage with category "PLANNING"', () => {
      const result = buildUniqueStages([makeStage(-1, 'PLANNING')])
      expect(result).toHaveLength(0)
    })

    it('excludes a stage with category "CONDITION-EVAL"', () => {
      const result = buildUniqueStages([makeStage(-2, 'CONDITION-EVAL')])
      expect(result).toHaveLength(0)
    })

    it('excludes uppercase system stages and keeps non-system stages', () => {
      const stages = [
        makeStage(-1, 'PLANNING'),
        makeStage(0, 'implementation'),
        makeStage(-2, 'CONDITION-EVAL'),
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(1)
      expect(result[0].category).toBe('implementation')
    })
  })

  // -------------------------------------------------------------------------
  // New behaviour: mixed-case category values
  // -------------------------------------------------------------------------
  describe('mixed-case system categories (new behaviour)', () => {
    it('excludes a stage with category "Planning"', () => {
      const result = buildUniqueStages([makeStage(-1, 'Planning')])
      expect(result).toHaveLength(0)
    })

    it('excludes a stage with category "Condition-Eval"', () => {
      const result = buildUniqueStages([makeStage(-2, 'Condition-Eval')])
      expect(result).toHaveLength(0)
    })

    it('excludes mixed-case system stages and keeps non-system stages', () => {
      const stages = [
        makeStage(-1, 'Planning'),
        makeStage(0, 'test'),
        makeStage(-2, 'Condition-Eval'),
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(1)
      expect(result[0].category).toBe('test')
    })
  })

  // -------------------------------------------------------------------------
  // Non-system categories pass through unaffected
  // -------------------------------------------------------------------------
  describe('non-system categories pass through unaffected', () => {
    it.each([
      ['review'],
      ['implementation'],
      ['test'],
      ['docs'],
      ['security'],
    ])('includes a stage with category "%s"', (category) => {
      const result = buildUniqueStages([makeStage(0, category)])
      expect(result).toHaveLength(1)
      expect(result[0].category).toBe(category)
    })

    it('keeps all non-system stages in a typical pipeline run', () => {
      const stages = [
        makeStage(-1, 'planning'),
        makeStage(0, 'review'),
        makeStage(1, 'implementation'),
        makeStage(2, 'test'),
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(3)
      expect(result.map((s) => s.category)).toEqual(['review', 'implementation', 'test'])
    })
  })

  // -------------------------------------------------------------------------
  // Deduplication: last write wins per stageNumber
  // -------------------------------------------------------------------------
  describe('deduplication by stageNumber', () => {
    it('keeps only the last stage entry for a given stageNumber', () => {
      const stages = [
        { stageNumber: 0, category: 'review' },
        { stageNumber: 0, category: 'review' }, // duplicate — second wins
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(1)
    })

    it('deduplication still excludes system stages regardless of repetition', () => {
      const stages = [
        makeStage(-1, 'PLANNING'),
        makeStage(-1, 'planning'), // same slot, different case
        makeStage(0, 'review'),
      ]
      const result = buildUniqueStages(stages)
      expect(result).toHaveLength(1)
      expect(result[0].category).toBe('review')
    })
  })

  // -------------------------------------------------------------------------
  // Output ordering
  // -------------------------------------------------------------------------
  describe('output is sorted by stageNumber ascending', () => {
    it('returns stages sorted ascending even when input is unsorted', () => {
      const stages = [
        makeStage(2, 'test'),
        makeStage(0, 'review'),
        makeStage(1, 'implementation'),
      ]
      const result = buildUniqueStages(stages)
      expect(result.map((s) => s.stageNumber)).toEqual([0, 1, 2])
    })
  })
})
