/**
 * GraphQL Query assembler.
 *
 * Combines domain-specific query resolvers into a single Query object.
 * Individual domains live in task-queries.ts and repo-queries.ts.
 *
 * Shared mapper functions (mapRepository, mapStage, mapAgentDefinition,
 * fetchAgentWithOverrides, getDrainStatus) live in mappers.ts and are
 * re-exported here for backward compatibility.
 */

import { Context } from '../context.js'
import { taskQueries } from './task-queries.js'
import { repoQueries } from './repo-queries.js'
import { getDrainStatus } from './mappers.js'

// Re-export shared mappers so existing import paths still work.
export {
  mapTask,
  mapAgentDefinition,
  fetchAgentWithOverrides,
  mapRepository,
  mapStage,
  getDrainStatus,
} from './mappers.js'

export const Query = {
  // Task queries
  ...taskQueries,

  // Repository, agent, auth, pipeline queries
  ...repoQueries,

  // Drain status (cross-domain)
  async drainStatus(_: unknown, __: unknown, ctx: Context) {
    return getDrainStatus(ctx.pool)
  },
}
