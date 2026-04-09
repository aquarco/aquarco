/**
 * Tests for api/src/resolvers/helpers.ts — shared utility functions
 * extracted during the codebase simplification refactoring (#109).
 *
 * Covers the overloaded toDbEnum, payload builders, scope validation,
 * and spec validation.
 */

import { describe, it, expect } from '@jest/globals'
import {
  toDbEnum,
  taskPayload,
  errorPayload,
  repoErrorPayload,
  agentErrorPayload,
  prErrorPayload,
  validateScope,
  SCOPE_PATTERN,
  validateSpec,
  VALID_SPEC_KEYS,
} from '../resolvers/helpers.js'

// ── toDbEnum ─────────────────────────────────────────────────────────────────

describe('toDbEnum', () => {
  it('converts uppercase to lowercase', () => {
    expect(toDbEnum('PENDING')).toBe('pending')
  })

  it('converts mixed case to lowercase', () => {
    expect(toDbEnum('InProgress')).toBe('inprogress')
  })

  it('returns lowercase unchanged', () => {
    expect(toDbEnum('completed')).toBe('completed')
  })

  it('returns null for null input (overload)', () => {
    expect(toDbEnum(null)).toBeNull()
  })

  it('returns null for undefined input (overload)', () => {
    expect(toDbEnum(undefined)).toBeNull()
  })

  it('returns null for empty string', () => {
    // empty string is falsy, so toDbEnum returns null
    expect(toDbEnum('')).toBeNull()
  })
})

// ── Payload builders ─────────────────────────────────────────────────────────

describe('errorPayload', () => {
  it('returns task null with error array', () => {
    const result = errorPayload('status', 'Invalid status')
    expect(result).toEqual({
      task: null,
      errors: [{ field: 'status', message: 'Invalid status' }],
    })
  })

  it('supports null field', () => {
    const result = errorPayload(null, 'General error')
    expect(result.errors[0].field).toBeNull()
  })
})

describe('repoErrorPayload', () => {
  it('returns repository null with error array', () => {
    const result = repoErrorPayload('name', 'Required')
    expect(result).toEqual({
      repository: null,
      errors: [{ field: 'name', message: 'Required' }],
    })
  })
})

describe('agentErrorPayload', () => {
  it('returns agent null with error array', () => {
    const result = agentErrorPayload('spec', 'Invalid')
    expect(result).toEqual({
      agent: null,
      errors: [{ field: 'spec', message: 'Invalid' }],
    })
  })
})

describe('prErrorPayload', () => {
  it('returns prUrl null with field null', () => {
    const result = prErrorPayload('PR creation failed')
    expect(result).toEqual({
      prUrl: null,
      errors: [{ field: null, message: 'PR creation failed' }],
    })
  })
})

// ── validateScope ────────────────────────────────────────────────────────────

describe('validateScope', () => {
  it('accepts "global"', () => {
    expect(validateScope('global')).toBeNull()
  })

  it('accepts "repo:<name>"', () => {
    expect(validateScope('repo:my-project')).toBeNull()
  })

  it('accepts repo with dots and underscores', () => {
    expect(validateScope('repo:my_project.v2')).toBeNull()
  })

  it('rejects empty string', () => {
    expect(validateScope('')).not.toBeNull()
  })

  it('rejects "local"', () => {
    expect(validateScope('local')).toContain('Invalid scope')
  })

  it('rejects "repo:" without name', () => {
    expect(validateScope('repo:')).not.toBeNull()
  })
})

// ── validateSpec ─────────────────────────────────────────────────────────────

describe('validateSpec', () => {
  const validSpec = {
    categories: ['test'],
    promptInline: 'Hello',
    priority: 10,
  }

  it('accepts a valid spec', () => {
    expect(validateSpec(validSpec)).toBeNull()
  })

  it('rejects non-object', () => {
    expect(validateSpec('string')).toContain('JSON object')
  })

  it('rejects null', () => {
    expect(validateSpec(null)).toContain('JSON object')
  })

  it('rejects array', () => {
    expect(validateSpec([])).toContain('JSON object')
  })

  it('rejects missing categories', () => {
    expect(validateSpec({ promptInline: 'x' })).toContain('categories')
  })

  it('rejects unknown keys', () => {
    expect(validateSpec({ categories: ['x'], promptInline: 'x', badKey: true })).toContain('unknown key')
  })

  it('rejects spec without any prompt source', () => {
    expect(validateSpec({ categories: ['x'] })).toContain('promptFile')
  })

  it('accepts spec with promptFile instead of promptInline', () => {
    expect(validateSpec({ categories: ['x'], promptFile: 'agent.md' })).toBeNull()
  })
})
