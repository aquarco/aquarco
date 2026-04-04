import { timingSafeEqual } from 'node:crypto'
import { IncomingMessage } from 'node:http'
import { Pool } from 'pg'
import { createLoaders, Loaders } from './loaders.js'

export interface Context {
  pool: Pool
  loaders: Loaders
  req: IncomingMessage
}

/**
 * Verify that the request carries a valid internal API key.
 * When AQUARCO_INTERNAL_API_KEY is set, the caller must send it
 * in the X-API-Key header. When unset (dev mode), all requests pass.
 */
export function requireInternalAuth(ctx: Context): void {
  const expected = process.env.AQUARCO_INTERNAL_API_KEY
  if (!expected) return // no key configured — dev/internal mode
  const provided = ctx.req.headers['x-api-key']
  if (
    typeof provided !== 'string' ||
    provided.length !== expected.length ||
    !timingSafeEqual(Buffer.from(provided), Buffer.from(expected))
  ) {
    throw new Error('Unauthorized: missing or invalid X-API-Key header')
  }
}

export function createContext(pool: Pool, req: IncomingMessage): Context {
  return {
    pool,
    loaders: createLoaders(pool),
    req,
  }
}
