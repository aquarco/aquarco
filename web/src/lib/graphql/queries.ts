import { gql } from '@apollo/client'

// Domain-specific re-exports for backward compatibility
export * from './task-queries'
export * from './repo-queries'
export * from './agent-queries'

// ── Dashboard (cross-domain — kept here) ─────────────────────────────────────

export const DASHBOARD_STATS = gql`
  query DashboardStats {
    dashboardStats {
      totalTasks
      pendingTasks
      executingTasks
      completedTasks
      failedTasks
      blockedTasks
      activeAgents
      totalTokensToday
      totalCostToday
      tasksByPipeline { pipeline count }
      tasksByRepository { repository count }
    }
  }
`

export const TOKEN_USAGE_BY_MODEL = gql`
  query TokenUsageByModel($days: Int) {
    tokenUsageByModel(days: $days) {
      day model tokensInput tokensOutput cacheReadTokens cacheWriteTokens costUsd
    }
  }
`
