import { Pool } from 'pg'
import { createLoaders, Loaders } from './loaders.js'

export interface Context {
  pool: Pool
  loaders: Loaders
}

export function createContext(pool: Pool): Context {
  return {
    pool,
    loaders: createLoaders(pool),
  }
}
