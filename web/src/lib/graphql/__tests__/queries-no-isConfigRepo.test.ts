/**
 * Tests verifying that isConfigRepo and SET_CONFIG_REPO have been fully removed
 * from the GraphQL query/mutation definitions.
 *
 * Context: PR #99 removed the config repo overlay system from the schema.
 * Commit 8708642 cleaned up the remaining frontend references. These tests
 * guard against accidental re-introduction.
 */

import { describe, it, expect } from 'vitest'
import { print } from 'graphql'
import * as queries from '../queries'

// ── SET_CONFIG_REPO removal ──────────────────────────────────────────────────

describe('SET_CONFIG_REPO removal', () => {
  it('should not export SET_CONFIG_REPO', () => {
    expect((queries as Record<string, unknown>).SET_CONFIG_REPO).toBeUndefined()
  })
})

// ── isConfigRepo field removal from existing queries ─────────────────────────

describe('isConfigRepo field removal', () => {
  it('GET_REPOSITORIES should not request isConfigRepo', () => {
    const queryText = print(queries.GET_REPOSITORIES)
    expect(queryText).not.toContain('isConfigRepo')
  })

  it('REGISTER_REPOSITORY should not request isConfigRepo', () => {
    const mutationText = print(queries.REGISTER_REPOSITORY)
    expect(mutationText).not.toContain('isConfigRepo')
  })

  it('no exported query or mutation should reference isConfigRepo', () => {
    const exportedKeys = Object.keys(queries)
    for (const key of exportedKeys) {
      const value = (queries as Record<string, unknown>)[key]
      // Only check DocumentNode objects (gql-tagged templates)
      if (value && typeof value === 'object' && 'kind' in (value as object)) {
        const text = print(value as Parameters<typeof print>[0])
        expect(text, `${key} still references isConfigRepo`).not.toContain('isConfigRepo')
      }
    }
  })
})

// ── Sanity: key queries still present and well-formed ────────────────────────

describe('repository queries sanity checks', () => {
  it('GET_REPOSITORIES should still query core fields', () => {
    const text = print(queries.GET_REPOSITORIES)
    expect(text).toContain('name')
    expect(text).toContain('url')
    expect(text).toContain('branch')
    expect(text).toContain('cloneStatus')
    expect(text).toContain('pollers')
    expect(text).toContain('taskCount')
  })

  it('REGISTER_REPOSITORY should still return core fields', () => {
    const text = print(queries.REGISTER_REPOSITORY)
    expect(text).toContain('name')
    expect(text).toContain('url')
    expect(text).toContain('branch')
    expect(text).toContain('pollers')
    expect(text).toContain('cloneStatus')
  })

  it('REMOVE_REPOSITORY should still be exported', () => {
    expect(queries.REMOVE_REPOSITORY).toBeDefined()
    const text = print(queries.REMOVE_REPOSITORY)
    expect(text).toContain('removeRepository')
  })

  it('RETRY_CLONE should still be exported', () => {
    expect(queries.RETRY_CLONE).toBeDefined()
    const text = print(queries.RETRY_CLONE)
    expect(text).toContain('retryClone')
  })
})
