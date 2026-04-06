/**
 * Tests for spending display utilities (web/src/lib/spending.ts).
 *
 * Covers the formatTokens() and formatCost() functions, with emphasis on
 * the null/undefined/zero guard added for issue #82 (show tokens count).
 */

import { describe, it, expect } from 'vitest'
import { formatTokens, formatCost } from '../spending'

// ── formatTokens ──────────────────────────────────────────────────────────────

describe('formatTokens', () => {
  it('should return em-dash for null', () => {
    expect(formatTokens(null)).toBe('—')
  })

  it('should return em-dash for undefined', () => {
    expect(formatTokens(undefined)).toBe('—')
  })

  it('should return em-dash for zero', () => {
    expect(formatTokens(0)).toBe('—')
  })

  it('should return the number as string for values < 1000', () => {
    expect(formatTokens(1)).toBe('1')
    expect(formatTokens(500)).toBe('500')
    expect(formatTokens(999)).toBe('999')
  })

  it('should format values >= 1000 with "k" suffix', () => {
    expect(formatTokens(1000)).toBe('1.0k')
    expect(formatTokens(1500)).toBe('1.5k')
    expect(formatTokens(15000)).toBe('15.0k')
    expect(formatTokens(999999)).toBe('1000.0k')
  })

  it('should format values >= 1_000_000 with "M" suffix', () => {
    expect(formatTokens(1_000_000)).toBe('1.0M')
    expect(formatTokens(2_500_000)).toBe('2.5M')
    expect(formatTokens(10_000_000)).toBe('10.0M')
  })

  it('should handle boundary between k and M', () => {
    // 999_999 is < 1M, so should be k
    expect(formatTokens(999_999)).toBe('1000.0k')
    // 1_000_000 is >= 1M
    expect(formatTokens(1_000_000)).toBe('1.0M')
  })
})

// ── formatCost ────────────────────────────────────────────────────────────────

describe('formatCost', () => {
  it('should return em-dash for null', () => {
    expect(formatCost(null)).toBe('—')
  })

  it('should return em-dash for undefined', () => {
    expect(formatCost(undefined)).toBe('—')
  })

  it('should return em-dash for zero', () => {
    expect(formatCost(0)).toBe('—')
  })

  it('should format small values (< $0.01) with 4 decimal places', () => {
    expect(formatCost(0.001)).toBe('$0.0010')
    expect(formatCost(0.0099)).toBe('$0.0099')
  })

  it('should format normal values with 2 decimal places', () => {
    expect(formatCost(0.01)).toBe('$0.01')
    expect(formatCost(1.5)).toBe('$1.50')
    expect(formatCost(42.99)).toBe('$42.99')
  })
})
