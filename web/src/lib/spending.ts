/**
 * Spending display utilities for task/stage cost and token formatting.
 */

export function formatCost(usd: number | null | undefined): string {
  if (usd == null || usd === 0) return '—'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function formatTokens(count: number | null | undefined): string {
  if (count == null || count === 0) return '—'
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}
