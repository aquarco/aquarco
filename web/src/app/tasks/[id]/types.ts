export interface Stage {
  id: string
  stageNumber: number
  iteration: number
  run: number
  executionOrder: number | null
  category: string
  agent: string | null
  agentVersion: string | null
  status: string
  startedAt: string | null
  completedAt: string | null
  structuredOutput: unknown | null
  tokensInput: number | null
  tokensOutput: number | null
  costUsd: number | null
  cacheReadTokens: number | null
  cacheWriteTokens: number | null
  errorMessage: string | null
  retryCount: number
  liveOutput: string | null
}

export interface ContextEntry {
  id: string
  key: string
  valueType: string
  valueJson: unknown | null
  valueText: string | null
  valueFileRef: string | null
  createdAt: string
  stageNumber: number | null
}

export interface Task {
  id: string
  title: string
  status: string
  priority: number
  source: string
  sourceRef: string | null
  pipeline: string
  repository: { name: string }
  createdAt: string
  updatedAt: string
  startedAt: string | null
  completedAt: string | null
  lastCompletedStageId: number | null
  checkpointData: Record<string, unknown> | null
  pipelineVersion: string | null
  retryCount: number
  errorMessage: string | null
  parentTaskId: string | null
  prNumber: number | null
  branchName: string | null
  stages: Stage[]
  context: ContextEntry[]
}

export interface PipelineCondition {
  type: string
  expression: string
  onYes: string | null
  onNo: string | null
  maxRepeats: number | null
}

export interface PipelineStageDefn {
  name: string
  category: string
  required: boolean
  conditions: PipelineCondition[]
}
