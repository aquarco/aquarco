// GitHub API helper for creating PRs with modified agent definitions
//
// Reads the GitHub token from /agent-ssh/github-token (same as github-auth.ts)
// and uses GitHub REST API to create branches + PRs.

import { Pool } from 'pg'
import yaml from 'js-yaml'

const TOKEN_FILE = '/agent-ssh/github-token'

async function getGitHubToken(): Promise<string> {
  const fs = await import('node:fs/promises')
  try {
    const token = (await fs.readFile(TOKEN_FILE, 'utf-8')).trim()
    if (!token) throw new Error('GitHub token is empty')
    return token
  } catch {
    throw new Error(
      'No GitHub token available. Please log in via GitHub auth first.'
    )
  }
}

async function githubFetch(
  url: string,
  token: string,
  options: RequestInit = {}
): Promise<Response> {
  const res = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github.v3+json',
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> | undefined),
    },
  })
  return res
}

interface ModifiedAgent {
  agentName: string
  baseSpec: Record<string, unknown>
  modifiedSpec: Record<string, unknown>
  description: string
  version: string
  source: string
}

/**
 * Create a GitHub PR with all pending agent modifications for a given scope.
 *
 * For global agents: scope = 'global', repoName = the global config repo name
 * For repo agents: scope = 'repo:<name>', repoName = the repo name
 */
export async function createBranchAndPR(
  repoName: string,
  pool: Pool
): Promise<string> {
  const token = await getGitHubToken()

  const scope = `repo:${repoName}`

  // Get all modified agents for this scope
  const modifiedResult = await pool.query<Record<string, unknown>>(
    `SELECT
       ao.agent_name,
       ao.modified_spec,
       ad.spec AS base_spec,
       ad.description,
       ad.version,
       ad.source
     FROM agent_overrides ao
     JOIN agent_definitions ad ON ad.name = ao.agent_name AND ad.is_active = true
     WHERE ao.scope = $1 AND ao.modified_spec IS NOT NULL`,
    [scope]
  )

  if (modifiedResult.rows.length === 0) {
    throw new Error('No modified agents found for this repository')
  }

  const agents: ModifiedAgent[] = modifiedResult.rows.map((row) => ({
    agentName: row.agent_name as string,
    baseSpec: row.base_spec as Record<string, unknown>,
    modifiedSpec: row.modified_spec as Record<string, unknown>,
    description: row.description as string,
    version: row.version as string,
    source: row.source as string,
  }))

  // Parse the repo URL to get owner/repo
  const repoUrl = repo.url as string
  const match = repoUrl.match(/github\.com[/:]([^/]+)\/([^/.]+)/)
  if (!match) {
    throw new Error(`Cannot parse GitHub owner/repo from URL: ${repoUrl}`)
  }
  const [, owner, repoSlug] = match

  // Get the default branch from repo metadata
  const repoMetaRes = await githubFetch(
    `https://api.github.com/repos/${owner}/${repoSlug}`,
    token
  )
  if (!repoMetaRes.ok) {
    throw new Error(`Cannot access repository ${owner}/${repoSlug}`)
  }
  const repoMeta = (await repoMetaRes.json()) as { default_branch: string }
  const baseBranch = repoMeta.default_branch

  const refRes = await githubFetch(
    `https://api.github.com/repos/${owner}/${repoSlug}/git/ref/heads/${baseBranch}`,
    token
  )
  if (!refRes.ok) {
    throw new Error(`Cannot find branch "${baseBranch}"`)
  }
  const refData = (await refRes.json()) as { object: { sha: string } }
  const baseSha = refData.object.sha

  // Create a new branch
  const branchName = `agent-modifications-${Date.now()}`
  const createRefRes = await githubFetch(
    `https://api.github.com/repos/${owner}/${repoSlug}/git/refs`,
    token,
    {
      method: 'POST',
      body: JSON.stringify({
        ref: `refs/heads/${branchName}`,
        sha: baseSha,
      }),
    }
  )
  if (!createRefRes.ok) {
    const err = await createRefRes.text()
    throw new Error(`Failed to create branch: ${err}`)
  }

  // Create/update files and PR — clean up branch on failure
  try {
    for (const agent of agents) {
      const filePath = `config/agents/definitions/${agent.agentName}.yaml`

      // Build the full agent YAML document
      const agentDoc = {
        apiVersion: 'aquarco.agents/v1',
        kind: 'AgentDefinition',
        metadata: {
          name: agent.agentName,
          version: agent.version,
          description: agent.description,
        },
        spec: agent.modifiedSpec,
      }
      const content = yaml.dump(agentDoc, { lineWidth: -1, noRefs: true })
      const base64Content = Buffer.from(content).toString('base64')

      // Check if file exists to get its SHA
      const existingRes = await githubFetch(
        `https://api.github.com/repos/${owner}/${repoSlug}/contents/${filePath}?ref=${branchName}`,
        token
      )
      const body: Record<string, unknown> = {
        message: `chore: update agent definition for ${agent.agentName}`,
        content: base64Content,
        branch: branchName,
      }
      if (existingRes.ok) {
        const existing = (await existingRes.json()) as { sha: string }
        body.sha = existing.sha
      }

      const updateRes = await githubFetch(
        `https://api.github.com/repos/${owner}/${repoSlug}/contents/${filePath}`,
        token,
        {
          method: 'PUT',
          body: JSON.stringify(body),
        }
      )
      if (!updateRes.ok) {
        const err = await updateRes.text()
        throw new Error(`Failed to update file ${filePath}: ${err}`)
      }
    }

    // Create the PR
    const agentNames = agents.map((a) => a.agentName).join(', ')
    const prRes = await githubFetch(
      `https://api.github.com/repos/${owner}/${repoSlug}/pulls`,
      token,
      {
        method: 'POST',
        body: JSON.stringify({
          title: `chore: update agent definitions (${agents.length} agents)`,
          body: `This PR updates agent definitions modified via the Aquarco UI.\n\nModified agents: ${agentNames}`,
          head: branchName,
          base: baseBranch,
        }),
      }
    )
    if (!prRes.ok) {
      const err = await prRes.text()
      throw new Error(`Failed to create PR: ${err}`)
    }

    const prData = (await prRes.json()) as { html_url: string }
    return prData.html_url
  } catch (err) {
    // Best-effort cleanup: delete the orphaned branch
    try {
      await githubFetch(
        `https://api.github.com/repos/${owner}/${repoSlug}/git/refs/heads/${branchName}`,
        token,
        { method: 'DELETE' }
      )
    } catch { /* ignore cleanup failure */ }
    throw err
  }
}
