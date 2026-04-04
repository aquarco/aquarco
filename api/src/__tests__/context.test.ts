/**
 * Tests for api/src/context.ts — requireInternalAuth and createContext
 *
 * Verifies:
 *   - Dev mode: no API key configured → all requests pass
 *   - Valid key: timing-safe comparison succeeds
 *   - Invalid key: wrong key throws Unauthorized
 *   - Missing header: throws Unauthorized
 *   - Length mismatch: throws Unauthorized (no timing-safe comparison attempted)
 *   - Non-string header value: throws Unauthorized
 *   - createContext: returns correct shape
 */

import { jest, describe, it, expect, beforeEach, afterEach } from '@jest/globals'
import { requireInternalAuth, createContext } from '../context.js'
import type { Context } from '../context.js'
import type { IncomingMessage } from 'node:http'
import type { Pool } from 'pg'

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeFakeReq(headers: Record<string, string | string[] | undefined> = {}): IncomingMessage {
  return { headers } as unknown as IncomingMessage
}

function makeFakeCtx(headers: Record<string, string | string[] | undefined> = {}): Context {
  return {
    pool: {} as Pool,
    loaders: {} as Context['loaders'],
    req: makeFakeReq(headers),
  }
}

// ── requireInternalAuth ──────────────────────────────────────────────────────

describe('requireInternalAuth', () => {
  const originalEnv = process.env.AQUARCO_INTERNAL_API_KEY

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.AQUARCO_INTERNAL_API_KEY
    } else {
      process.env.AQUARCO_INTERNAL_API_KEY = originalEnv
    }
  })

  it('should pass when no API key is configured (dev mode)', () => {
    delete process.env.AQUARCO_INTERNAL_API_KEY
    const ctx = makeFakeCtx({})

    // Should not throw
    expect(() => requireInternalAuth(ctx)).not.toThrow()
  })

  it('should pass when valid API key is provided', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'secret-key-123'
    const ctx = makeFakeCtx({ 'x-api-key': 'secret-key-123' })

    expect(() => requireInternalAuth(ctx)).not.toThrow()
  })

  it('should throw when API key is wrong', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'secret-key-123'
    const ctx = makeFakeCtx({ 'x-api-key': 'wrong-key-456' })

    expect(() => requireInternalAuth(ctx)).toThrow('Unauthorized')
  })

  it('should throw when X-API-Key header is missing', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'secret-key-123'
    const ctx = makeFakeCtx({})

    expect(() => requireInternalAuth(ctx)).toThrow('Unauthorized')
  })

  it('should throw when key length does not match (short-circuits before timingSafeEqual)', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'long-secret-key'
    const ctx = makeFakeCtx({ 'x-api-key': 'short' })

    expect(() => requireInternalAuth(ctx)).toThrow('Unauthorized')
  })

  it('should throw when header value is not a string (e.g., array)', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'secret-key-123'
    // HTTP headers can be string[] for repeated headers
    const ctx = makeFakeCtx({ 'x-api-key': ['val1', 'val2'] as unknown as string })

    expect(() => requireInternalAuth(ctx)).toThrow('Unauthorized')
  })

  it('should throw when header value is undefined', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = 'secret-key-123'
    const ctx = makeFakeCtx({ 'x-api-key': undefined })

    expect(() => requireInternalAuth(ctx)).toThrow('Unauthorized')
  })

  it('should pass with empty string key when env is empty string', () => {
    process.env.AQUARCO_INTERNAL_API_KEY = ''
    const ctx = makeFakeCtx({})

    // Empty string is falsy, so it returns early (dev mode)
    expect(() => requireInternalAuth(ctx)).not.toThrow()
  })
})

// ── createContext ─────────────────────────────────────────────────────────────

describe('createContext', () => {
  it('should return an object with pool, loaders, and req', () => {
    const fakePool = { query: jest.fn() } as unknown as Pool
    const fakeReq = makeFakeReq({ host: 'localhost' })

    const ctx = createContext(fakePool, fakeReq)

    expect(ctx.pool).toBe(fakePool)
    expect(ctx.req).toBe(fakeReq)
    expect(ctx.loaders).toBeDefined()
    expect(ctx.loaders.repositoryLoader).toBeDefined()
  })
})
