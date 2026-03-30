/**
 * Standalone test runner for the system category filter logic.
 * Uses Node.js built-in assert — no external test framework required.
 *
 * Run: node run-tests.mjs
 */

import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Logic under test — mirrors page.tsx lines 480-486
// ---------------------------------------------------------------------------
function buildUniqueStages(stages) {
  const SYSTEM_CATEGORIES = new Set(['planning', 'condition-eval'])
  const uniqueStagesMap = new Map()
  for (const s of stages) {
    if (SYSTEM_CATEGORIES.has(s.category.toLowerCase())) continue
    uniqueStagesMap.set(s.stageNumber, s)
  }
  return Array.from(uniqueStagesMap.values()).sort((a, b) => a.stageNumber - b.stageNumber)
}

function makeStage(stageNumber, category) {
  return { stageNumber, category }
}

// ---------------------------------------------------------------------------
// Minimal test harness
// ---------------------------------------------------------------------------
let passed = 0
let failed = 0

function test(name, fn) {
  try {
    fn()
    console.log(`  ✓ ${name}`)
    passed++
  } catch (err) {
    console.log(`  ✗ ${name}`)
    console.log(`    ${err.message}`)
    failed++
  }
}

function describe(name, fn) {
  console.log(`\n${name}`)
  fn()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('lowercase system categories (existing behaviour)', () => {
  test('excludes a stage with category "planning"', () => {
    const result = buildUniqueStages([makeStage(-1, 'planning')])
    assert.equal(result.length, 0)
  })

  test('excludes a stage with category "condition-eval"', () => {
    const result = buildUniqueStages([makeStage(-2, 'condition-eval')])
    assert.equal(result.length, 0)
  })

  test('excludes all system stages and keeps only the non-system stage', () => {
    const stages = [makeStage(-1, 'planning'), makeStage(0, 'review'), makeStage(-2, 'condition-eval')]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 1)
    assert.equal(result[0].category, 'review')
  })
})

describe('uppercase system categories (new behaviour)', () => {
  test('excludes a stage with category "PLANNING"', () => {
    const result = buildUniqueStages([makeStage(-1, 'PLANNING')])
    assert.equal(result.length, 0)
  })

  test('excludes a stage with category "CONDITION-EVAL"', () => {
    const result = buildUniqueStages([makeStage(-2, 'CONDITION-EVAL')])
    assert.equal(result.length, 0)
  })

  test('excludes uppercase system stages and keeps non-system stages', () => {
    const stages = [makeStage(-1, 'PLANNING'), makeStage(0, 'implementation'), makeStage(-2, 'CONDITION-EVAL')]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 1)
    assert.equal(result[0].category, 'implementation')
  })
})

describe('mixed-case system categories (new behaviour)', () => {
  test('excludes a stage with category "Planning"', () => {
    const result = buildUniqueStages([makeStage(-1, 'Planning')])
    assert.equal(result.length, 0)
  })

  test('excludes a stage with category "Condition-Eval"', () => {
    const result = buildUniqueStages([makeStage(-2, 'Condition-Eval')])
    assert.equal(result.length, 0)
  })

  test('excludes mixed-case system stages and keeps non-system stages', () => {
    const stages = [makeStage(-1, 'Planning'), makeStage(0, 'test'), makeStage(-2, 'Condition-Eval')]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 1)
    assert.equal(result[0].category, 'test')
  })
})

describe('non-system categories pass through unaffected', () => {
  for (const category of ['review', 'implementation', 'test', 'docs', 'security']) {
    test(`includes a stage with category "${category}"`, () => {
      const result = buildUniqueStages([makeStage(0, category)])
      assert.equal(result.length, 1)
      assert.equal(result[0].category, category)
    })
  }

  test('keeps all non-system stages in a typical pipeline run', () => {
    const stages = [
      makeStage(-1, 'planning'),
      makeStage(0, 'review'),
      makeStage(1, 'implementation'),
      makeStage(2, 'test'),
    ]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 3)
    assert.deepEqual(result.map((s) => s.category), ['review', 'implementation', 'test'])
  })
})

describe('deduplication by stageNumber', () => {
  test('keeps only the last stage entry for a given stageNumber', () => {
    const stages = [makeStage(0, 'review'), makeStage(0, 'review')]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 1)
  })

  test('deduplication still excludes system stages regardless of repetition', () => {
    const stages = [makeStage(-1, 'PLANNING'), makeStage(-1, 'planning'), makeStage(0, 'review')]
    const result = buildUniqueStages(stages)
    assert.equal(result.length, 1)
    assert.equal(result[0].category, 'review')
  })
})

describe('output is sorted by stageNumber ascending', () => {
  test('returns stages sorted ascending even when input is unsorted', () => {
    const stages = [makeStage(2, 'test'), makeStage(0, 'review'), makeStage(1, 'implementation')]
    const result = buildUniqueStages(stages)
    assert.deepEqual(result.map((s) => s.stageNumber), [0, 1, 2])
  })
})

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
const total = passed + failed
console.log(`\n${'─'.repeat(50)}`)
console.log(`Tests: ${total} total, ${passed} passed, ${failed} failed`)
if (failed > 0) {
  process.exit(1)
}
