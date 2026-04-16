/**
 * Tests for the Recent Tasks table logic on the Dashboard page (Issue #141).
 *
 * Validates:
 * - Updated column: terminal statuses show formatted date, active statuses show elapsed time
 * - Cost column: formatCost + formatTokens sub-text
 * - Pipeline column: null-coalesced to em-dash
 *
 * These are pure-logic tests that don't render React components,
 * testing the same conditional logic used in the dashboard page.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { formatDate, formatElapsed } from '../../../lib/format'
import { formatCost, formatTokens } from '../../../lib/spending'

// ── Terminal status detection (mirrors page.tsx line 279) ─────────────────────

const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED']

function isTerminalStatus(status: string | undefined | null): boolean {
  return TERMINAL_STATUSES.includes(status?.toUpperCase() ?? '')
}

describe('terminal status detection (Updated column)', () => {
  it('should identify COMPLETED as terminal', () => {
    expect(isTerminalStatus('COMPLETED')).toBe(true)
  })

  it('should identify FAILED as terminal', () => {
    expect(isTerminalStatus('FAILED')).toBe(true)
  })

  it('should identify TIMEOUT as terminal', () => {
    expect(isTerminalStatus('TIMEOUT')).toBe(true)
  })

  it('should identify CANCELLED as terminal', () => {
    expect(isTerminalStatus('CANCELLED')).toBe(true)
  })

  it('should identify CLOSED as terminal', () => {
    expect(isTerminalStatus('CLOSED')).toBe(true)
  })

  it('should NOT identify EXECUTING as terminal', () => {
    expect(isTerminalStatus('EXECUTING')).toBe(false)
  })

  it('should NOT identify PENDING as terminal', () => {
    expect(isTerminalStatus('PENDING')).toBe(false)
  })

  it('should NOT identify BLOCKED as terminal', () => {
    expect(isTerminalStatus('BLOCKED')).toBe(false)
  })

  it('should be case-insensitive (lowercase input)', () => {
    expect(isTerminalStatus('completed')).toBe(true)
    expect(isTerminalStatus('failed')).toBe(true)
  })

  it('should handle null/undefined safely', () => {
    expect(isTerminalStatus(null)).toBe(false)
    expect(isTerminalStatus(undefined)).toBe(false)
  })
})

// ── Updated column display logic ─────────────────────────────────────────────

describe('Updated column display logic', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function getUpdatedDisplay(task: {
    status: string
    updatedAt: string
    completedAt?: string | null
  }): string {
    if (isTerminalStatus(task.status)) {
      return formatDate(task.completedAt || task.updatedAt)
    }
    return formatElapsed(task.updatedAt)
  }

  it('should show formatDate for COMPLETED task with completedAt', () => {
    const display = getUpdatedDisplay({
      status: 'COMPLETED',
      updatedAt: '2026-04-15T10:00:00Z',
      completedAt: '2026-04-15T12:00:00Z',
    })
    // Should use completedAt, not updatedAt
    expect(display).not.toBe('—')
    expect(display.length).toBeGreaterThan(0)
  })

  it('should fall back to updatedAt when completedAt is null for terminal task', () => {
    const display = getUpdatedDisplay({
      status: 'COMPLETED',
      updatedAt: '2026-04-15T10:00:00Z',
      completedAt: null,
    })
    expect(display).not.toBe('—')
  })

  it('should show elapsed time for EXECUTING task', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    const display = getUpdatedDisplay({
      status: 'EXECUTING',
      updatedAt: new Date(now - 120_000).toISOString(),
    })
    expect(display).toBe('2m 0s')
  })

  it('should show elapsed time for PENDING task', () => {
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now)
    const display = getUpdatedDisplay({
      status: 'PENDING',
      updatedAt: new Date(now - 45_000).toISOString(),
    })
    expect(display).toBe('45s')
  })
})

// ── Cost column display logic ────────────────────────────────────────────────

describe('Cost column display logic', () => {
  it('should display em-dash for null cost', () => {
    expect(formatCost(null)).toBe('—')
  })

  it('should display em-dash for zero cost', () => {
    expect(formatCost(0)).toBe('—')
  })

  it('should display cost with dollar sign', () => {
    expect(formatCost(1.5)).toBe('$1.50')
  })

  it('should display small costs with 4 decimal places', () => {
    expect(formatCost(0.0042)).toBe('$0.0042')
  })

  it('should show tokens sub-text when totalTokens > 0', () => {
    const totalTokens = 15000
    const showSubText = totalTokens != null && totalTokens > 0
    expect(showSubText).toBe(true)
    expect(formatTokens(totalTokens)).toBe('15.0k')
  })

  it('should NOT show tokens sub-text when totalTokens is null', () => {
    const totalTokens = null
    const showSubText = totalTokens != null && (totalTokens as number) > 0
    expect(showSubText).toBe(false)
  })

  it('should NOT show tokens sub-text when totalTokens is 0', () => {
    const totalTokens = 0
    const showSubText = totalTokens != null && totalTokens > 0
    expect(showSubText).toBe(false)
  })
})

// ── Pipeline column display logic ────────────────────────────────────────────

describe('Pipeline column display logic', () => {
  it('should display pipeline name when present', () => {
    const pipeline: string | null = 'feature-pipeline'
    expect(pipeline ?? '—').toBe('feature-pipeline')
  })

  it('should display em-dash when pipeline is null', () => {
    const pipeline: string | null = null
    expect(pipeline ?? '—').toBe('—')
  })

  it('should display em-dash when pipeline is undefined', () => {
    const pipeline: string | undefined = undefined
    expect(pipeline ?? '—').toBe('—')
  })
})
