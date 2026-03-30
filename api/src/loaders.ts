import DataLoader from 'dataloader'
import { Pool } from 'pg'

// Raw DB row shapes (snake_case from PostgreSQL)
export interface RepositoryRow {
  name: string
  url: string
  branch: string
  clone_dir: string
  pollers: string[]
  last_cloned_at: string | null
  last_pulled_at: string | null
  clone_status: string
  head_sha: string | null
}

export interface StageRow {
  id: string
  task_id: string
  stage_number: number
  iteration: number | null
  run: number | null
  category: string
  agent: string | null
  agent_version: string | null
  status: string
  started_at: string | null
  completed_at: string | null
  structured_output: unknown | null
  raw_output: string | null
  tokens_input: number | null
  tokens_output: number | null
  cost_usd: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  error_message: string | null
  retry_count: number
  live_output: string | null
}

export interface ContextRow {
  id: string
  task_id: string
  stage_number: number | null
  key: string
  value_type: string
  value_json: unknown | null
  value_text: string | null
  value_file_ref: string | null
  created_at: string
}

export interface Loaders {
  repositoryLoader: DataLoader<string, RepositoryRow | null>
  stagesByTaskLoader: DataLoader<string, StageRow[]>
  contextByTaskLoader: DataLoader<string, ContextRow[]>
}

export function createLoaders(pool: Pool): Loaders {
  const repositoryLoader = new DataLoader<string, RepositoryRow | null>(
    async (names) => {
      const result = await pool.query<RepositoryRow>(
        'SELECT * FROM repositories WHERE name = ANY($1)',
        [names as string[]]
      )
      const byName = new Map<string, RepositoryRow>()
      for (const row of result.rows) {
        byName.set(row.name, row)
      }
      return names.map((name) => byName.get(name) ?? null)
    },
    { cache: true }
  )

  const stagesByTaskLoader = new DataLoader<string, StageRow[]>(
    async (taskIds) => {
      // Return all stage runs in chronological order so the UI can display the full execution history.
      const result = await pool.query<StageRow>(
        `SELECT s.*
         FROM stages s
         WHERE s.task_id = ANY($1)
         ORDER BY s.task_id, s.stage_number ASC, COALESCE(s.iteration, 1) ASC, COALESCE(s.run, 1) ASC`,
        [taskIds as string[]]
      )
      const byTaskId = new Map<string, StageRow[]>()
      for (const row of result.rows) {
        const list = byTaskId.get(row.task_id) ?? []
        list.push(row)
        byTaskId.set(row.task_id, list)
      }
      return taskIds.map((id) => byTaskId.get(id) ?? [])
    },
    { cache: true }
  )

  const contextByTaskLoader = new DataLoader<string, ContextRow[]>(
    async (taskIds) => {
      const result = await pool.query<ContextRow>(
        'SELECT * FROM context WHERE task_id = ANY($1) ORDER BY task_id, created_at ASC',
        [taskIds as string[]]
      )
      const byTaskId = new Map<string, ContextRow[]>()
      for (const row of result.rows) {
        const list = byTaskId.get(row.task_id) ?? []
        list.push(row)
        byTaskId.set(row.task_id, list)
      }
      return taskIds.map((id) => byTaskId.get(id) ?? [])
    },
    { cache: true }
  )

  return {
    repositoryLoader,
    stagesByTaskLoader,
    contextByTaskLoader,
  }
}
