/**
 * Shared utility functions used by mutation resolvers.
 * Payload builders, enum converters, and validators.
 */

import { mapTask } from './mappers.js'

// GraphQL enum values are UPPER_CASE; DB stores lower_case
export function toDbEnum(value: string): string {
  return value.toLowerCase()
}

export function taskPayload(task: Record<string, unknown>) {
  return { task: mapTask(task), errors: [] }
}

export function errorPayload(field: string | null, message: string) {
  return { task: null, errors: [{ field, message }] }
}

export function repoErrorPayload(field: string | null, message: string) {
  return { repository: null, errors: [{ field, message }] }
}

export function agentErrorPayload(field: string | null, message: string) {
  return { agent: null, errors: [{ field, message }] }
}

export function prErrorPayload(message: string) {
  return { prUrl: null, errors: [{ field: null, message }] }
}

export const SCOPE_PATTERN = /^(global|repo:[a-zA-Z0-9._-]+)$/
export function validateScope(scope: string): string | null {
  if (!SCOPE_PATTERN.test(scope)) return `Invalid scope "${scope}". Must be "global" or "repo:<name>".`
  return null
}

export const VALID_SPEC_KEYS = new Set([
  'categories', 'priority', 'promptFile', 'promptInline', 'tools', 'resources',
  'environment', 'output', 'outputSchema', 'healthCheck', 'conditions',
])
const REQUIRED_SPEC_KEYS = ['categories']
const MAX_SPEC_SIZE = 100 * 1024

export function validateSpec(spec: unknown): string | null {
  if (typeof spec !== 'object' || spec === null || Array.isArray(spec)) return 'Spec must be a JSON object'
  if (JSON.stringify(spec).length > MAX_SPEC_SIZE) return 'Spec exceeds 100KB size limit'
  const keys = Object.keys(spec)
  for (const k of REQUIRED_SPEC_KEYS) { if (!keys.includes(k)) return `Spec missing required key "${k}"` }
  for (const k of keys) { if (!VALID_SPEC_KEYS.has(k)) return `Spec contains unknown key "${k}"` }
  // Require at least one prompt source
  if (!keys.includes('promptFile') && !keys.includes('promptInline')) {
    return 'Spec must contain either "promptFile" or "promptInline"'
  }
  return null
}
