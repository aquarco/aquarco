/**
 * Tests for Apollo Client URL routing logic in apollo.tsx.
 *
 * Validates the Caddy reverse proxy integration:
 * - Browser clients use relative /api/graphql (routed through Caddy proxy)
 * - SSR clients use direct http://api:4000/graphql (container-to-container)
 *
 * Issue: https://github.com/aquarco/aquarco/issues/2
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ---------------------------------------------------------------------------
// Helpers to simulate the URL resolution logic from apollo.tsx
// We extract the pure logic to test it without React/Apollo dependencies.
// ---------------------------------------------------------------------------

/**
 * Mirrors the URI resolution logic from makeClient() in apollo.tsx:
 *
 *   const uri =
 *     typeof window !== 'undefined'
 *       ? (process.env.NEXT_PUBLIC_API_URL ?? '/api/graphql')
 *       : 'http://api:4000/graphql'
 */
function resolveApiUri(
  isWindowDefined: boolean,
  envApiUrl: string | undefined,
): string {
  return isWindowDefined
    ? (envApiUrl ?? '/api/graphql')
    : 'http://api:4000/graphql'
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Apollo API URL resolution', () => {
  describe('browser environment (window is defined)', () => {
    it('should default to /api/graphql when NEXT_PUBLIC_API_URL is not set', () => {
      const uri = resolveApiUri(true, undefined)
      expect(uri).toBe('/api/graphql')
    })

    it('should use NEXT_PUBLIC_API_URL when set', () => {
      const uri = resolveApiUri(true, '/api/graphql')
      expect(uri).toBe('/api/graphql')
    })

    it('should allow custom NEXT_PUBLIC_API_URL override', () => {
      const uri = resolveApiUri(true, 'https://custom-api.example.com/graphql')
      expect(uri).toBe('https://custom-api.example.com/graphql')
    })

    it('should use relative URL for Caddy proxy routing', () => {
      const uri = resolveApiUri(true, undefined)
      // Must be relative (no protocol/host) so browser sends to same origin
      expect(uri).not.toMatch(/^https?:\/\//)
      // Must start with /api/ to match Caddy's handle_path /api/* route
      expect(uri).toMatch(/^\/api\//)
    })

    it('should use empty string NEXT_PUBLIC_API_URL when explicitly set to empty', () => {
      // Empty string is falsy but not nullish — ?? won't trigger
      const uri = resolveApiUri(true, '')
      expect(uri).toBe('')
    })
  })

  describe('SSR environment (window is undefined)', () => {
    it('should use direct container URL http://api:4000/graphql', () => {
      const uri = resolveApiUri(false, undefined)
      expect(uri).toBe('http://api:4000/graphql')
    })

    it('should ignore NEXT_PUBLIC_API_URL in SSR context', () => {
      const uri = resolveApiUri(false, '/api/graphql')
      expect(uri).toBe('http://api:4000/graphql')
    })

    it('should use Docker service name "api" for container-to-container communication', () => {
      const uri = resolveApiUri(false, undefined)
      expect(uri).toContain('api:4000')
    })

    it('should use port 4000 (GraphQL API internal port)', () => {
      const uri = resolveApiUri(false, undefined)
      const url = new URL(uri)
      expect(url.port).toBe('4000')
    })

    it('should NOT use /api/ prefix in SSR (no proxy, direct connection)', () => {
      const uri = resolveApiUri(false, undefined)
      const url = new URL(uri)
      expect(url.pathname).toBe('/graphql')
    })
  })
})

describe('Apollo URL routing contract with Caddy', () => {
  it('browser path /api/graphql maps to Caddy handle_path /api/* route', () => {
    const uri = resolveApiUri(true, undefined)
    // Caddy strips /api prefix via handle_path, so /api/graphql -> /graphql on api:4000
    expect(uri).toBe('/api/graphql')
  })

  it('SSR bypasses Caddy entirely with direct container address', () => {
    const uri = resolveApiUri(false, undefined)
    // SSR runs inside Docker network, goes directly to api container
    expect(uri).toBe('http://api:4000/graphql')
    expect(uri).not.toContain('/api/')
  })
})
