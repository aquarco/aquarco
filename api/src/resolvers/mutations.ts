/**
 * GraphQL Mutation assembler.
 *
 * Combines domain-specific mutation resolvers into a single Mutation object.
 * Individual domains live in task-mutations.ts, repo-mutations.ts, and agent-mutations.ts.
 */

import { taskMutations } from './task-mutations.js'
import { repoMutations } from './repo-mutations.js'
import { agentMutations } from './agent-mutations.js'

export const Mutation = {
  // Auth mutations (lightweight, kept inline)
  async githubLoginStart() {
    const { startDeviceFlow } = await import('../github-auth.js')
    return startDeviceFlow()
  },

  async githubLoginPoll() {
    const { pollDeviceFlow } = await import('../github-auth.js')
    return pollDeviceFlow()
  },

  async githubLogout() {
    const { logout } = await import('../github-auth.js')
    return logout()
  },

  async claudeLoginStart() {
    const { startClaudeLogin } = await import('../claude-auth.js')
    return startClaudeLogin()
  },

  async claudeLoginPoll() {
    const { pollClaudeLogin } = await import('../claude-auth.js')
    return pollClaudeLogin()
  },

  async claudeSubmitCode(_: unknown, args: { code: string }) {
    const { submitClaudeCode } = await import('../claude-auth.js')
    const result = await submitClaudeCode(args.code)
    return { success: result.success, email: null, error: result.error }
  },

  async claudeLogout() {
    const { claudeLogout } = await import('../claude-auth.js')
    return claudeLogout()
  },

  // Task mutations
  ...taskMutations,

  // Repository mutations
  ...repoMutations,

  // Agent mutations
  ...agentMutations,
}
