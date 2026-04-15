/**
 * Tests for format utilities (web/src/lib/format.ts).
 *
 * Validates Issue #141: the Updated column in the Recent Tasks table uses
 * formatDate() for terminal states and formatElapsed() for active states.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { formatDate, formatElapsed, formatNumber } from '../format'

// ── formatDate ──────────────────────────────────────────────────────────────

describe('formatDate', () => {
  it('should return em-dash for null', () => {
    expect(formatDate(null)).toBe('—')
  })

  it('should return em-dash for undefined', () => {
    expect(formatDate(undefined)).toBe('—')
  })

  it('should return em-dash for empty string', () => {
    expect(formatDate('')).toBe('—')
  })

  it('should format a valid ISO date string', () => {
    const result = formatDate('2026-04-15T12:30:00Z')
    // The result depends on locale, but it should be a non-empty string that's not em-dash
    expect(result).not.toBe('—')
    expect(result.length).toBeGreaterThan(0)
  })
})

// ── formatElapsed ───────────────────────────────────────────────────────────

describe('formatElapsed', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('should return em-dash for null', () => {
    expect(formatElapsed(null)).toBe('—')
  })

  it('should return em-dash for undefined', () => {
    expect(formatElapsed(undefined)).toBe('—')
  })

  it('should return em-dash for empty string', () => {
    expect(formatElapsed('')).toBe('—')
  })

  it('should format seconds for very recent timestamps', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    // 30 seconds ago
    const iso = new Date(now - 30_000).toISOString()
    expect(formatElapsed(iso)).toBe('30s')
  })

  it('should format minutes and seconds', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    // 5 minutes 12 seconds ago
    const iso = new Date(now - (5 * 60 + 12) * 1000).toISOString()
    expect(formatElapsed(iso)).toBe('5m 12s')
  })

  it('should format hours and minutes', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    // 2 hours 15 minutes ago
    const iso = new Date(now - (2 * 3600 + 15 * 60) * 1000).toISOString()
    expect(formatElapsed(iso)).toBe('2h 15m')
  })

  it('should format days and hours', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    // 3 days 5 hours ago
    const iso = new Date(now - (3 * 86400 + 5 * 3600) * 1000).toISOString()
    expect(formatElapsed(iso)).toBe('3d 5h')
  })

  it('should return 0s for a timestamp exactly now', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    const iso = new Date(now).toISOString()
    expect(formatElapsed(iso)).toBe('0s')
  })
})

// ── formatNumber ────────────────────────────────────────────────────────────

describe('formatNumber', () => {
  it('should return em-dash for null', () => {
    expect(formatNumber(null)).toBe('—')
  })

  it('should return em-dash for undefined', () => {
    expect(formatNumber(undefined)).toBe('—')
  })

  it('should format a number with locale separators', () => {
    const result = formatNumber(1234567)
    // Should contain the digits and some separator
    expect(result).toContain('1')
    expect(result).toContain('234')
    expect(result).toContain('567')
  })

  it('should handle zero', () => {
    expect(formatNumber(0)).toBe('0')
  })
})
