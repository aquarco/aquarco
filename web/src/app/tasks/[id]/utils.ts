/**
 * Utility functions for Task detail page.
 *
 * Extracted from page.tsx so they can be unit-tested without copy-paste duplication.
 */

// ── Live Output Parser ──────────────────────────────────────────────────────

export function parseLiveOutput(liveOutput: string): string[] {
  const results: string[] = []
  for (const line of liveOutput.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      continue // skip non-JSON lines
    }

    // Top-level stdout / output
    if (typeof parsed.stdout === 'string' && parsed.stdout) results.push(parsed.stdout)
    if (typeof parsed.output === 'string' && parsed.output) results.push(parsed.output)

    // message.content array fields
    const msgContent = (parsed.message as Record<string, unknown> | undefined)?.content
    if (Array.isArray(msgContent)) {
      for (const c of msgContent) {
        if (typeof c !== 'object' || c == null) continue
        const item = c as Record<string, unknown>
        if (typeof item.thinking === 'string' && item.thinking) results.push(item.thinking)
        if (typeof item.text === 'string' && item.text) results.push(item.text)
        if (typeof item.content === 'string' && item.content) results.push(item.content)
        const input = item.input as Record<string, unknown> | undefined
        if (input) {
          if (typeof input.description === 'string' && input.description) results.push(input.description)
          if (typeof input.file_path === 'string' && input.file_path) results.push(input.file_path)
        }
      }
    }

    // tool_use_result
    const tur = parsed.tool_use_result
    if (typeof tur === 'string' && tur) {
      results.push(tur)
    } else if (typeof tur === 'object' && tur != null) {
      const t = tur as Record<string, unknown>
      if (typeof t.stdout === 'string' && t.stdout) results.push(t.stdout)
      if (typeof t.stderr === 'string' && t.stderr) results.push(t.stderr)
      if (typeof t.content === 'string' && t.content) results.push(t.content)
      const f = t.file as Record<string, unknown> | undefined
      if (f && typeof f.filePath === 'string' && f.filePath) results.push(f.filePath)
    }
  }
  return results
}

// ── Structured Output Display ───────────────────────────────────────────────

export function toSectionTitle(key: string): string {
  // snake_case → Title Case, camelCase → Title Case
  return key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export interface FindingItem {
  severity?: string
  file?: string
  line?: number
  message?: string
}

export function isFindingArray(arr: unknown[]): arr is FindingItem[] {
  if (arr.length === 0) return false
  return arr.every(
    (item) => typeof item === 'object' && item != null && 'message' in item && 'severity' in item,
  )
}

// ── Duration Formatting ─────────────────────────────────────────────────────

export function formatDurationSeconds(totalSeconds: number): string {
  // Guard against non-finite or negative values
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return '0s'
  const rounded = Math.floor(totalSeconds)
  if (rounded < 60) return `${rounded}s`
  const minutes = Math.floor(rounded / 60)
  const secs = rounded % 60
  if (minutes < 60) return `${minutes}m ${secs}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}
