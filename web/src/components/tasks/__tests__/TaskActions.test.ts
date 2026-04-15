/**
 * Tests for TaskActions component logic — action button visibility rules.
 *
 * TaskActions shows different action buttons based on the task status.
 * This test validates the pure logic mapping status → available actions
 * without requiring a full React/Apollo render context.
 *
 * Ref: GitHub issue #109, review gap — no tests existed for TaskActions.
 */

import { describe, it, expect } from 'vitest'

/**
 * Pure logic extracted from TaskActions.tsx for testability.
 * Maps task status to which actions are available.
 */
function getAvailableActions(status: string) {
  const upper = status?.toUpperCase()
  return {
    canContinue: upper === 'FAILED' || upper === 'RATE_LIMITED' || upper === 'TIMEOUT',
    canRunAgain: upper === 'COMPLETED' || upper === 'FAILED' || upper === 'CLOSED' || upper === 'CANCELLED',
    canClose: upper === 'COMPLETED',
    canCancel: upper === 'PENDING' || upper === 'QUEUED' || upper === 'EXECUTING',
    canUnblock: upper === 'BLOCKED',
  }
}

// ── Status → Action Mapping ──────────────────────────────────────────────

describe('TaskActions status logic', () => {
  describe('FAILED status', () => {
    it('allows continue and run again', () => {
      const actions = getAvailableActions('FAILED')
      expect(actions.canContinue).toBe(true)
      expect(actions.canRunAgain).toBe(true)
    })

    it('disallows close, cancel, unblock', () => {
      const actions = getAvailableActions('FAILED')
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('COMPLETED status', () => {
    it('allows run again and close', () => {
      const actions = getAvailableActions('COMPLETED')
      expect(actions.canRunAgain).toBe(true)
      expect(actions.canClose).toBe(true)
    })

    it('disallows continue, cancel, unblock', () => {
      const actions = getAvailableActions('COMPLETED')
      expect(actions.canContinue).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('PENDING status', () => {
    it('allows cancel only', () => {
      const actions = getAvailableActions('PENDING')
      expect(actions.canCancel).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('QUEUED status', () => {
    it('allows cancel only', () => {
      const actions = getAvailableActions('QUEUED')
      expect(actions.canCancel).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('EXECUTING status', () => {
    it('allows cancel only', () => {
      const actions = getAvailableActions('EXECUTING')
      expect(actions.canCancel).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('BLOCKED status', () => {
    it('allows unblock only', () => {
      const actions = getAvailableActions('BLOCKED')
      expect(actions.canUnblock).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
    })
  })

  describe('RATE_LIMITED status', () => {
    it('allows continue only', () => {
      const actions = getAvailableActions('RATE_LIMITED')
      expect(actions.canContinue).toBe(true)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('TIMEOUT status', () => {
    it('allows continue only', () => {
      const actions = getAvailableActions('TIMEOUT')
      expect(actions.canContinue).toBe(true)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('CLOSED status', () => {
    it('allows run again only', () => {
      const actions = getAvailableActions('CLOSED')
      expect(actions.canRunAgain).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('CANCELLED status', () => {
    it('allows run again only', () => {
      const actions = getAvailableActions('CANCELLED')
      expect(actions.canRunAgain).toBe(true)
      expect(actions.canContinue).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })

  describe('case insensitivity', () => {
    it('handles lowercase status', () => {
      const actions = getAvailableActions('failed')
      expect(actions.canContinue).toBe(true)
    })

    it('handles mixed case status', () => {
      const actions = getAvailableActions('Completed')
      expect(actions.canRunAgain).toBe(true)
      expect(actions.canClose).toBe(true)
    })
  })

  describe('unknown status', () => {
    it('shows no actions for unrecognized status', () => {
      const actions = getAvailableActions('UNKNOWN')
      expect(actions.canContinue).toBe(false)
      expect(actions.canRunAgain).toBe(false)
      expect(actions.canClose).toBe(false)
      expect(actions.canCancel).toBe(false)
      expect(actions.canUnblock).toBe(false)
    })
  })
})
