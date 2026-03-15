import { Pool, QueryResult, QueryResultRow } from 'pg'

if (!process.env.DATABASE_URL) {
  console.error('DATABASE_URL environment variable is not set')
  process.exit(1)
}

export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  // Reasonable defaults for a containerized service
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
})

pool.on('error', (err) => {
  console.error('Unexpected PostgreSQL pool error:', err.message)
})

pool.on('connect', () => {
  // Set the search_path for every new connection so aifishtank schema is default
  pool.query("SET search_path TO aifishtank, public").catch((err) => {
    console.error('Failed to set search_path on new connection:', err.message)
  })
})

export async function query<R extends QueryResultRow = QueryResultRow>(
  sql: string,
  params?: unknown[]
): Promise<QueryResult<R>> {
  const client = await pool.connect()
  try {
    // Ensure search_path is set for this query
    await client.query('SET search_path TO aifishtank, public')
    return await client.query<R>(sql, params)
  } finally {
    client.release()
  }
}
