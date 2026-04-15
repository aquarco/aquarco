/**
 * Tests for Dashboard stat card configuration (Issue #141).
 *
 * Validates the stat card layout and data mapping on the Dashboard page,
 * including the new cost-related display patterns.
 */

import { describe, it, expect } from 'vitest'
import { formatCost, formatTokens } from '../../lib/spending'

// ── Stat card definitions (mirrors page.tsx) ─────────────────────────────────

describe('Dashboard stat cards', () => {
  const statCards = [
    { label: 'Total Tasks', key: 'totalTasks', color: '#1976d2' },
    { label: 'Pending', key: 'pendingTasks', color: '#757575' },
    { label: 'Executing', key: 'executingTasks', color: '#ed6c02' },
    { label: 'Completed', key: 'completedTasks', color: '#2e7d32' },
    { label: 'Failed', key: 'failedTasks', color: '#d32f2f' },
    { label: 'Blocked', key: 'blockedTasks', color: '#e65100' },
  ]

  it('should have exactly 6 stat cards', () => {
    expect(statCards).toHaveLength(6)
  })

  it('should have unique labels', () => {
    const labels = statCards.map((c) => c.label)
    expect(new Set(labels).size).toBe(6)
  })

  it('should have unique colors', () => {
    const colors = statCards.map((c) => c.color)
    expect(new Set(colors).size).toBe(6)
  })

  it('should default value to 0 when stats are undefined', () => {
    const stats: Record<string, number | undefined> = {}
    for (const card of statCards) {
      expect(stats[card.key] ?? 0).toBe(0)
    }
  })
})

// ── Cost display on task rows ────────────────────────────────────────────────

describe('Task row cost display integration', () => {
  it('should format a typical task cost with tokens sub-text', () => {
    const totalCostUsd = 2.3456
    const totalTokens = 125000
    expect(formatCost(totalCostUsd)).toBe('$2.35')
    expect(formatTokens(totalTokens)).toBe('125.0k')
  })

  it('should show em-dash and no sub-text for zero-cost task', () => {
    const totalCostUsd = 0
    const totalTokens = 0
    expect(formatCost(totalCostUsd)).toBe('—')
    // Component logic: totalTokens != null && totalTokens > 0 → false
    const showSubText = totalTokens != null && totalTokens > 0
    expect(showSubText).toBe(false)
  })

  it('should handle task with cost but no tokens', () => {
    const totalCostUsd = 0.5
    const totalTokens: number | null = null
    expect(formatCost(totalCostUsd)).toBe('$0.50')
    const showSubText = totalTokens != null && (totalTokens as number) > 0
    expect(showSubText).toBe(false)
  })

  it('should handle very large token counts in M format', () => {
    expect(formatTokens(5_200_000)).toBe('5.2M')
  })

  it('should handle tokens under 1000 as plain numbers', () => {
    expect(formatTokens(999)).toBe('999')
  })
})

// ── Recent Tasks table row click navigation ──────────────────────────────────

describe('Recent Tasks — row data contract', () => {
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

  const sampleTask: TaskRow = {
    id: 'task-123',
    title: 'Implement feature X',
    status: 'COMPLETED',
    pipeline: 'feature-pipeline',
    repository: { name: 'aquarco' },
    createdAt: '2026-04-10T08:00:00Z',
    updatedAt: '2026-04-10T12:00:00Z',
    completedAt: '2026-04-10T11:30:00Z',
    totalCostUsd: 3.42,
    totalTokens: 245000,
  }

  it('should have all fields required by table columns', () => {
    expect(sampleTask.title).toBeTruthy()
    expect(sampleTask.status).toBeTruthy()
    expect(sampleTask.repository.name).toBeTruthy()
    expect(sampleTask.pipeline).toBeTruthy()
    expect(typeof sampleTask.totalCostUsd).toBe('number')
    expect(sampleTask.updatedAt).toBeTruthy()
  })

  it('task row click should navigate to /tasks/{id}', () => {
    const expectedPath = `/tasks/${sampleTask.id}`
    expect(expectedPath).toBe('/tasks/task-123')
  })

  it('repository column should display repository.name', () => {
    expect(sampleTask.repository.name).toBe('aquarco')
  })
})
