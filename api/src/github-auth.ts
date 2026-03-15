// GitHub Device Flow authentication
// Uses the gh CLI's public OAuth App client ID for device flow.

const GITHUB_CLIENT_ID = process.env.GITHUB_CLIENT_ID ?? '178c6fc778ccc68e1d6a'
const TOKEN_FILE = '/agent-ssh/github-token'

interface DeviceFlowState {
  deviceCode: string
  userCode: string
  verificationUri: string
  expiresAt: number
  interval: number
}

let pendingFlow: DeviceFlowState | null = null

export async function startDeviceFlow(): Promise<{
  userCode: string
  verificationUri: string
  expiresIn: number
}> {
  const res = await fetch('https://github.com/login/device/code', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      client_id: GITHUB_CLIENT_ID,
      scope: 'repo',
    }),
  })

  if (!res.ok) {
    throw new Error(`GitHub device code request failed: ${res.status}`)
  }

  const data = (await res.json()) as {
    device_code: string
    user_code: string
    verification_uri: string
    expires_in: number
    interval: number
  }

  pendingFlow = {
    deviceCode: data.device_code,
    userCode: data.user_code,
    verificationUri: data.verification_uri,
    expiresAt: Date.now() + data.expires_in * 1000,
    interval: data.interval,
  }

  return {
    userCode: data.user_code,
    verificationUri: data.verification_uri,
    expiresIn: data.expires_in,
  }
}

export async function pollDeviceFlow(): Promise<{
  success: boolean
  username: string | null
  error: string | null
}> {
  if (!pendingFlow) {
    return { success: false, username: null, error: 'No pending login flow. Call githubLoginStart first.' }
  }

  if (Date.now() > pendingFlow.expiresAt) {
    pendingFlow = null
    return { success: false, username: null, error: 'Login flow expired. Please start again.' }
  }

  const res = await fetch('https://github.com/login/oauth/access_token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      client_id: GITHUB_CLIENT_ID,
      device_code: pendingFlow.deviceCode,
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
    }),
  })

  if (!res.ok) {
    return { success: false, username: null, error: `GitHub token request failed: ${res.status}` }
  }

  const data = (await res.json()) as {
    access_token?: string
    error?: string
    error_description?: string
  }

  if (data.error === 'authorization_pending') {
    return { success: false, username: null, error: null }
  }

  if (data.error === 'slow_down') {
    return { success: false, username: null, error: null }
  }

  if (data.error) {
    pendingFlow = null
    return { success: false, username: null, error: data.error_description ?? data.error }
  }

  if (data.access_token) {
    // Store the token (chown to match the host agent user)
    const fs = await import('node:fs/promises')
    await fs.writeFile(TOKEN_FILE, data.access_token, { mode: 0o600 })
    const AGENT_UID = parseInt(process.env.AGENT_UID ?? '1001', 10)
    const AGENT_GID = parseInt(process.env.AGENT_GID ?? '1001', 10)
    await fs.chown(TOKEN_FILE, AGENT_UID, AGENT_GID).catch(() => {})

    // Get the username
    let username: string | null = null
    try {
      const userRes = await fetch('https://api.github.com/user', {
        headers: { Authorization: `Bearer ${data.access_token}` },
      })
      if (userRes.ok) {
        const user = (await userRes.json()) as { login: string }
        username = user.login
      }
    } catch {
      // non-critical
    }

    pendingFlow = null
    return { success: true, username, error: null }
  }

  return { success: false, username: null, error: 'Unexpected response from GitHub' }
}

export async function logout(): Promise<boolean> {
  const fs = await import('node:fs/promises')
  try {
    await fs.unlink(TOKEN_FILE)
  } catch {
    // file may not exist
  }
  return true
}

export async function getAuthStatus(): Promise<{
  authenticated: boolean
  username: string | null
}> {
  const fs = await import('node:fs/promises')

  let token: string
  try {
    token = (await fs.readFile(TOKEN_FILE, 'utf-8')).trim()
  } catch {
    return { authenticated: false, username: null }
  }

  if (!token) {
    return { authenticated: false, username: null }
  }

  try {
    const res = await fetch('https://api.github.com/user', {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.ok) {
      const user = (await res.json()) as { login: string }
      return { authenticated: true, username: user.login }
    }
    return { authenticated: false, username: null }
  } catch {
    return { authenticated: false, username: null }
  }
}
