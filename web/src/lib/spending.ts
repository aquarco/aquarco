/**
 * Client-side NDJSON spending parser for live stage output.
 * Mirrors the Python spending.py parser logic.
 */

export interface LiveSpending {
  inputTokens: number
  outputTokens: number
  cacheWriteTokens: number
  cacheReadTokens: number
  estimatedCostUsd: number
  model: string | null
}

// Model pricing per million tokens
const MODEL_PRICING: Record<string, { input: number; output: number; cacheWrite: number; cacheRead: number }> = {
  'opus-4.5': { input: 5, output: 25, cacheWrite: 6.25, cacheRead: 0.50 },
  'opus-4.6': { input: 5, output: 25, cacheWrite: 6.25, cacheRead: 0.50 },
  'sonnet': { input: 3, output: 15, cacheWrite: 3.75, cacheRead: 0.30 },
  'haiku-4.5': { input: 1, output: 5, cacheWrite: 1.25, cacheRead: 0.10 },
}
const DEFAULT_PRICING = MODEL_PRICING['sonnet']

function getPricing(model: string): typeof DEFAULT_PRICING {
  const lower = model.toLowerCase()
  if (lower.includes('opus')) {
    return MODEL_PRICING['opus-4.5']
  }
  if (lower.includes('haiku')) {
    return MODEL_PRICING['haiku-4.5']
  }
  return DEFAULT_PRICING
}

export function parseLiveSpending(ndjson: string): LiveSpending {
  let inputTokens = 0
  let outputTokens = 0
  let cacheWriteTokens = 0
  let cacheReadTokens = 0
  let model: string | null = null

  for (const line of ndjson.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    let msg: Record<string, unknown>
    try {
      msg = JSON.parse(trimmed)
    } catch {
      continue
    }
    if (typeof msg !== 'object' || msg === null) continue

    if (msg.type === 'system' && msg.subtype === 'init' && typeof msg.model === 'string') {
      model = msg.model
      continue
    }

    if (msg.type === 'assistant') {
      const message = msg.message as Record<string, unknown> | undefined
      const usage = message?.usage as Record<string, number> | undefined
      if (usage) {
        inputTokens += usage.input_tokens ?? 0
        outputTokens += usage.output_tokens ?? 0
        cacheWriteTokens += usage.cache_creation_input_tokens ?? 0
        cacheReadTokens += usage.cache_read_input_tokens ?? 0
        if (!model && typeof message?.model === 'string') {
          model = message.model
        }
      }
      continue
    }

    if (msg.type === 'result') {
      const usage = msg.usage as Record<string, number> | undefined
      if (usage) {
        inputTokens = usage.input_tokens ?? inputTokens
        outputTokens = usage.output_tokens ?? outputTokens
        cacheWriteTokens = usage.cache_creation_input_tokens ?? cacheWriteTokens
        cacheReadTokens = usage.cache_read_input_tokens ?? cacheReadTokens
      }
      if (typeof msg.total_cost_usd === 'number') {
        return { inputTokens, outputTokens, cacheWriteTokens, cacheReadTokens, estimatedCostUsd: msg.total_cost_usd, model }
      }
    }
  }

  const pricing = getPricing(model ?? '')
  const estimatedCostUsd =
    inputTokens * pricing.input / 1_000_000 +
    outputTokens * pricing.output / 1_000_000 +
    cacheWriteTokens * pricing.cacheWrite / 1_000_000 +
    cacheReadTokens * pricing.cacheRead / 1_000_000

  return { inputTokens, outputTokens, cacheWriteTokens, cacheReadTokens, estimatedCostUsd, model }
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null || usd === 0) return '—'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}
