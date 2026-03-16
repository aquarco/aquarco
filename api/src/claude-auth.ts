// Claude CLI authentication via file-based IPC.
//
// The API container cannot run the `claude` CLI directly (it's on the host).
// Instead, a host-side helper script (claude-auth-helper.sh) watches the
// shared /agent-ssh/claude-ipc/ directory for command files and executes
// the claude CLI on behalf of the API.
//
// Flow:
//   1. API writes a request file (e.g., "login-request")
//   2. Host helper picks it up, runs `claude auth login`, writes response
//   3. API polls for the response file

const IPC_DIR = '/claude-ipc'
const POLL_INTERVAL_MS = 500
const POLL_TIMEOUT_MS = 60_000

async function ensureIpcDir(): Promise<void> {
  const fs = await import('node:fs/promises')
  await fs.mkdir(IPC_DIR, { recursive: true })
}

async function requestAndWait(
  command: string,
  timeoutMs = POLL_TIMEOUT_MS
): Promise<string> {
  const fs = await import('node:fs/promises')
  await ensureIpcDir()

  const requestFile = `${IPC_DIR}/${command}-request`
  const responseFile = `${IPC_DIR}/${command}-response`

  // Clean up any stale response
  await fs.unlink(responseFile).catch(() => {})

  // Write request trigger
  await fs.writeFile(requestFile, Date.now().toString(), { mode: 0o644 })

  // Poll for response
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
    try {
      const data = await fs.readFile(responseFile, 'utf-8')
      if (data.trim()) {
        await fs.unlink(responseFile).catch(() => {})
        return data.trim()
      }
    } catch {
      // response not yet written
    }
  }

  throw new Error(`Timed out waiting for ${command} response from host`)
}

export async function startClaudeLogin(): Promise<{
  authorizeUrl: string
  expiresIn: number
}> {
  const raw = await requestAndWait('login', 45_000)
  let parsed: { authorizeUrl?: string; error?: string }

  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new Error(`Invalid response from auth helper: ${raw.slice(0, 200)}`)
  }

  if (parsed.error) {
    throw new Error(parsed.error)
  }

  if (!parsed.authorizeUrl) {
    throw new Error('No authorize URL in response')
  }

  return {
    authorizeUrl: parsed.authorizeUrl,
    expiresIn: 600,
  }
}

export async function pollClaudeLogin(): Promise<{
  success: boolean
  email: string | null
  error: string | null
}> {
  const raw = await requestAndWait('status', 10_000)
  let parsed: { loggedIn?: boolean; email?: string; account_email?: string; authMethod?: string }

  try {
    parsed = JSON.parse(raw)
  } catch {
    return { success: false, email: null, error: null }
  }

  if (parsed.loggedIn) {
    return {
      success: true,
      email: parsed.email ?? parsed.account_email ?? null,
      error: null,
    }
  }

  return { success: false, email: null, error: null }
}

export async function submitClaudeCode(code: string): Promise<{
  success: boolean
  error: string | null
}> {
  const fs = await import('node:fs/promises')
  await ensureIpcDir()

  // Write the auth code for the pexpect driver to pick up
  await fs.writeFile(`${IPC_DIR}/code-submit`, code, { mode: 0o644 })

  // Wait for the code-complete response
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
    try {
      const data = await fs.readFile(`${IPC_DIR}/code-complete`, 'utf-8')
      if (data.trim()) {
        await fs.unlink(`${IPC_DIR}/code-complete`).catch(() => {})
        const parsed = JSON.parse(data) as { success: boolean; error?: string }
        return { success: parsed.success, error: parsed.error ?? null }
      }
    } catch {
      // not yet written
    }
  }

  return { success: false, error: 'Timed out waiting for code verification' }
}

export async function claudeLogout(): Promise<boolean> {
  await requestAndWait('logout', 10_000)
  return true
}

export async function getClaudeAuthStatus(): Promise<{
  authenticated: boolean
  email: string | null
}> {
  try {
    const raw = await requestAndWait('status', 10_000)
    const parsed = JSON.parse(raw) as {
      loggedIn?: boolean
      email?: string
      account_email?: string
    }
    return {
      authenticated: parsed.loggedIn === true,
      email: parsed.email ?? parsed.account_email ?? null,
    }
  } catch {
    return { authenticated: false, email: null }
  }
}
