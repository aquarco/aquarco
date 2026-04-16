/**
 * Tests for Issue #141: Cost spending visualisation — GraphQL query structure.
 *
 * Validates that the queries used by the dashboard include all fields needed for:
 * 1. Token Usage chart dual-axis (costUsd in TOKEN_USAGE_BY_MODEL)
 * 2. Recent Tasks table columns (completedAt, totalCostUsd, totalTokens in GET_TASKS)
 */

import { describe, it, expect } from 'vitest'
import { print } from 'graphql'
import { TOKEN_USAGE_BY_MODEL, DASHBOARD_STATS, GET_TASKS } from '../queries.js'

// Helper: extract the printed query string
function queryString(doc: ReturnType<typeof TOKEN_USAGE_BY_MODEL>) {
  return print(doc)
}

// ── TOKEN_USAGE_BY_MODEL — costUsd field ─────────────────────────────────────

describe('TOKEN_USAGE_BY_MODEL query (Issue #141)', () => {
  it('should include costUsd field', () => {
    const q = queryString(TOKEN_USAGE_BY_MODEL)
    expect(q).toContain('costUsd')
  })

  it('should include all token fields alongside costUsd', () => {
    const q = queryString(TOKEN_USAGE_BY_MODEL)
    expect(q).toContain('tokensInput')
    expect(q).toContain('tokensOutput')
    expect(q).toContain('cacheReadTokens')
    expect(q).toContain('cacheWriteTokens')
    expect(q).toContain('costUsd')
  })

  it('should include day and model fields', () => {
    const q = queryString(TOKEN_USAGE_BY_MODEL)
    expect(q).toContain('day')
    expect(q).toContain('model')
  })

  it('should accept days variable', () => {
    const q = queryString(TOKEN_USAGE_BY_MODEL)
    expect(q).toContain('$days')
    expect(q).toContain('Int')
  })
})

// ── DASHBOARD_STATS — totalCostToday field ───────────────────────────────────

describe('DASHBOARD_STATS query (Issue #141)', () => {
  it('should include totalCostToday field', () => {
    const q = queryString(DASHBOARD_STATS)
    expect(q).toContain('totalCostToday')
  })

  it('should include totalTokensToday alongside cost', () => {
    const q = queryString(DASHBOARD_STATS)
    expect(q).toContain('totalTokensToday')
    expect(q).toContain('totalCostToday')
  })
})

// ── GET_TASKS — fields for Recent Tasks table ────────────────────────────────

describe('GET_TASKS query — Recent Tasks table fields (Issue #141)', () => {
  it('should include completedAt for Updated column logic', () => {
    const q = queryString(GET_TASKS)
    expect(q).toContain('completedAt')
  })

  it('should include totalCostUsd for Cost column', () => {
    const q = queryString(GET_TASKS)
    expect(q).toContain('totalCostUsd')
  })

  it('should include totalTokens for Cost column sub-text', () => {
    const q = queryString(GET_TASKS)
    expect(q).toContain('totalTokens')
  })

  it('should include updatedAt for Updated column', () => {
    const q = queryString(GET_TASKS)
    expect(q).toContain('updatedAt')
  })

  it('should include pipeline for Pipeline column', () => {
    const q = queryString(GET_TASKS)
    expect(q).toContain('pipeline')
  })

  it('should include all 6 table columns fields: title, status, repository, pipeline, totalCostUsd, updatedAt', () => {
    const q = queryString(GET_TASKS)
    for (const field of ['title', 'status', 'pipeline', 'totalCostUsd', 'updatedAt', 'completedAt', 'totalTokens']) {
      expect(q).toContain(field)
    }
    // repository is a nested object
    expect(q).toContain('repository')
    expect(q).toContain('name')
  })
})
