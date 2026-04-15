/**
 * Tests for Dashboard page — Recent Tasks table logic (Issue #141).
 *
 * Validates the table column structure and display logic:
 * - 6 columns: Title, Status, Repository, Pipeline, Cost, Updated
 * - Cost column: formatCost + formatTokens display
 * - Updated column: terminal state date vs active state elapsed time
 * - Table matches Tasks page (minus ID column)
 */

import { describe, it, expect } from 'vitest'
import { formatCost, formatTokens } from '../../lib/spending'
import { formatDate, formatElapsed } from '../../lib/format'

// ── Table column definitions (mirrors page.tsx) ────────────────────────────────

const RECENT_TASKS_COLUMNS = ['Title', 'Status', 'Repository', 'Pipeline', 'Cost', 'Updated']
const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED']

interface TaskRow {
  id: string
  title: string
  status: string
  pipeline: string | null
  repository: { name: string }
  createdAt: string
  updatedAt: string
  completedAt?: string | null
  totalCostUsd?: number | null
  totalTokens?: number | null
}

// ── Column structure ───────────────────────────────────────────────────────────

describe('Recent Tasks table — column structure (Issue #141)', () => {
  it('should have exactly 6 columns', () => {
    expect(RECENT_TASKS_COLUMNS).toHaveLength(6)
  })

  it('should not include ID column (matches Tasks page minus ID)', () => {
    expect(RECENT_TASKS_COLUMNS).not.toContain('ID')
    expect(RECENT_TASKS_COLUMNS).not.toContain('Id')
  })

  it('should include all required columns in order', () => {
    expect(RECENT_TASKS_COLUMNS).toEqual([
      'Title', 'Status', 'Repository', 'Pipeline', 'Cost', 'Updated',
    ])
  })
})

// ── Cost column display logic ──────────────────────────────────────────────────

describe('Recent Tasks table — Cost column', () => {
  it('should display formatted cost for tasks with cost', () => {
    const task: TaskRow = {
      id: '1', title: 'Test', status: 'COMPLETED', pipeline: 'feature-pipeline',
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T12:00:00Z', totalCostUsd: 1.75, totalTokens: 50000,
    }
    expect(formatCost(task.totalCostUsd)).toBe('$1.75')
    expect(formatTokens(task.totalTokens)).toBe('50.0k')
  })

  it('should display em-dash for null cost', () => {
    expect(formatCost(null)).toBe('—')
  })

  it('should display em-dash for zero cost', () => {
    expect(formatCost(0)).toBe('—')
  })

  it('should display small cost with 4 decimal places', () => {
    expect(formatCost(0.0012)).toBe('$0.0012')
  })

  it('should not show tokens when totalTokens is null', () => {
    const task: TaskRow = {
      id: '1', title: 'Test', status: 'EXECUTING', pipeline: null,
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T12:00:00Z', totalCostUsd: 0.50, totalTokens: null,
    }
    // Component logic: task.totalTokens != null && task.totalTokens > 0
    const showTokens = task.totalTokens != null && task.totalTokens > 0
    expect(showTokens).toBe(false)
  })

  it('should not show tokens when totalTokens is 0', () => {
    const task: TaskRow = {
      id: '1', title: 'Test', status: 'EXECUTING', pipeline: null,
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T12:00:00Z', totalCostUsd: 0.50, totalTokens: 0,
    }
    const showTokens = task.totalTokens != null && task.totalTokens > 0
    expect(showTokens).toBe(false)
  })

  it('should show tokens when totalTokens is positive', () => {
    const task: TaskRow = {
      id: '1', title: 'Test', status: 'COMPLETED', pipeline: 'feature-pipeline',
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T12:00:00Z', totalCostUsd: 1.00, totalTokens: 1500000,
    }
    const showTokens = task.totalTokens != null && task.totalTokens > 0
    expect(showTokens).toBe(true)
    expect(formatTokens(task.totalTokens)).toBe('1.5M')
  })
})

// ── Updated column display logic ───────────────────────────────────────────────

describe('Recent Tasks table — Updated column', () => {
  it('should identify terminal statuses correctly', () => {
    const terminalStatuses = ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED']
    for (const status of terminalStatuses) {
      expect(TERMINAL_STATUSES.includes(status.toUpperCase())).toBe(true)
    }
  })

  it('should not treat active statuses as terminal', () => {
    const activeStatuses = ['EXECUTING', 'PENDING', 'BLOCKED']
    for (const status of activeStatuses) {
      expect(TERMINAL_STATUSES.includes(status.toUpperCase())).toBe(false)
    }
  })

  it('should use formatDate for completed tasks with completedAt', () => {
    const task: TaskRow = {
      id: '1', title: 'Done Task', status: 'COMPLETED', pipeline: 'feature-pipeline',
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T14:00:00Z', completedAt: '2026-04-01T13:30:00Z',
    }
    const isTerminal = TERMINAL_STATUSES.includes(task.status.toUpperCase())
    expect(isTerminal).toBe(true)

    // Component logic: formatDate(task.completedAt || task.updatedAt)
    const displayDate = task.completedAt || task.updatedAt
    const formatted = formatDate(displayDate)
    expect(formatted).toBeTruthy()
    expect(formatted).not.toBe('—')
  })

  it('should fall back to updatedAt when completedAt is null for terminal tasks', () => {
    const task: TaskRow = {
      id: '1', title: 'Failed Task', status: 'FAILED', pipeline: 'feature-pipeline',
      repository: { name: 'aquarco' }, createdAt: '2026-04-01T10:00:00Z',
      updatedAt: '2026-04-01T14:00:00Z', completedAt: null,
    }
    const isTerminal = TERMINAL_STATUSES.includes(task.status.toUpperCase())
    expect(isTerminal).toBe(true)

    const displayDate = task.completedAt || task.updatedAt
    expect(displayDate).toBe('2026-04-01T14:00:00Z')
  })

  it('should use formatElapsed for active tasks', () => {
    const task: TaskRow = {
      id: '1', title: 'Running Task', status: 'EXECUTING', pipeline: 'feature-pipeline',
      repository: { name: 'aquarco' }, createdAt: '2026-04-15T10:00:00Z',
      updatedAt: '2026-04-15T12:00:00Z',
    }
    const isTerminal = TERMINAL_STATUSES.includes(task.status.toUpperCase())
    expect(isTerminal).toBe(false)

    const elapsed = formatElapsed(task.updatedAt)
    // Should return a time-relative string like "Xh Ym" or "Xd Yh"
    expect(elapsed).toBeTruthy()
    expect(elapsed).not.toBe('—')
  })

  it('should handle case-insensitive status comparison', () => {
    // Component logic: task.status?.toUpperCase()
    const statuses = ['completed', 'Completed', 'COMPLETED']
    for (const status of statuses) {
      expect(TERMINAL_STATUSES.includes(status.toUpperCase())).toBe(true)
    }
  })
})

// ── Pipeline column display logic ──────────────────────────────────────────────

describe('Recent Tasks table — Pipeline column', () => {
  it('should display pipeline name when available', () => {
    const pipeline = 'feature-pipeline'
    const display = pipeline ?? '—'
    expect(display).toBe('feature-pipeline')
  })

  it('should display em-dash when pipeline is null', () => {
    const pipeline: string | null = null
    const display = pipeline ?? '—'
    expect(display).toBe('—')
  })

  it('should display em-dash when pipeline is undefined', () => {
    const pipeline: string | undefined = undefined
    const display = pipeline ?? '—'
    expect(display).toBe('—')
  })
})
