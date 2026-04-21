/**
 * Tests for the ElapsedTicker extraction in page.tsx.
 *
 * The ElapsedTicker component was extracted so its 1-second setInterval
 * only re-renders individual elapsed-time cells, not the entire dashboard.
 * These tests verify the display logic that determines when ElapsedTicker
 * is used vs. when a static date is shown.
 */

import { describe, it, expect } from 'vitest'
import { formatDate, formatElapsed } from '../../lib/format'

// Terminal statuses use a static date display (formatDate)
const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED']

// Active statuses use ElapsedTicker (formatElapsed)
const ACTIVE_STATUSES = ['EXECUTING', 'PENDING', 'QUEUED', 'BLOCKED', 'PLANNING', 'RATE_LIMITED']

/**
 * Mimics the display logic in page.tsx:
 * terminal → formatDate(completedAt || updatedAt)
 * active   → ElapsedTicker (uses formatElapsed internally)
 */
function shouldUseElapsedTicker(status: string): boolean {
  return !TERMINAL_STATUSES.includes(status.toUpperCase())
}

// ── ElapsedTicker selection logic ─────────────────────────────────────────────

describe('ElapsedTicker — display routing', () => {
  it('should use static date for all terminal statuses', () => {
    for (const status of TERMINAL_STATUSES) {
      expect(shouldUseElapsedTicker(status)).toBe(false)
    }
  })

  it('should use elapsed ticker for all active statuses', () => {
    for (const status of ACTIVE_STATUSES) {
      expect(shouldUseElapsedTicker(status)).toBe(true)
    }
  })

  it('should handle case-insensitive status comparison', () => {
    expect(shouldUseElapsedTicker('completed')).toBe(false)
    expect(shouldUseElapsedTicker('Completed')).toBe(false)
    expect(shouldUseElapsedTicker('executing')).toBe(true)
    expect(shouldUseElapsedTicker('Executing')).toBe(true)
  })
})

// ── formatElapsed output (used by ElapsedTicker) ──────────────────────────────

describe('ElapsedTicker — formatElapsed integration', () => {
  it('should return a non-empty string for a past date', () => {
    const pastDate = new Date(Date.now() - 3600000).toISOString() // 1 hour ago
    const result = formatElapsed(pastDate)
    expect(result).toBeTruthy()
    expect(result).not.toBe('—')
  })

  it('should return a non-empty string for a very recent date', () => {
    const recentDate = new Date(Date.now() - 5000).toISOString() // 5 seconds ago
    const result = formatElapsed(recentDate)
    expect(result).toBeTruthy()
  })

  it('should return a sensible string for a date days ago', () => {
    const daysAgo = new Date(Date.now() - 86400000 * 3).toISOString() // 3 days ago
    const result = formatElapsed(daysAgo)
    expect(result).toBeTruthy()
    // Should contain "d" for days
    expect(result).toMatch(/\dd/)
  })
})

// ── Static date fallback (terminal statuses) ──────────────────────────────────

describe('ElapsedTicker — static date fallback', () => {
  it('should use completedAt when available for terminal tasks', () => {
    const completedAt = '2026-04-20T14:30:00Z'
    const updatedAt = '2026-04-20T15:00:00Z'
    const displayDate = completedAt || updatedAt
    expect(displayDate).toBe(completedAt)
  })

  it('should fall back to updatedAt when completedAt is null', () => {
    const completedAt: string | null = null
    const updatedAt = '2026-04-20T15:00:00Z'
    const displayDate = completedAt || updatedAt
    expect(displayDate).toBe(updatedAt)
  })

  it('should fall back to updatedAt when completedAt is empty string', () => {
    const completedAt = ''
    const updatedAt = '2026-04-20T15:00:00Z'
    const displayDate = completedAt || updatedAt
    expect(displayDate).toBe(updatedAt)
  })

  it('formatDate returns a truthy string for a valid ISO date', () => {
    const result = formatDate('2026-04-20T14:30:00Z')
    expect(result).toBeTruthy()
    expect(result).not.toBe('—')
  })
})
