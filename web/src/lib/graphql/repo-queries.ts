import { gql } from '@apollo/client'

// ── Repositories ──────────────────────────────────────────────────────────────

export const GET_REPOSITORIES = gql`
  query GetRepositories {
    repositories {
      name
      url
      branch
      cloneDir
      pollers
      lastClonedAt
      lastPulledAt
      cloneStatus
      headSha
      errorMessage
      deployPublicKey
      taskCount
      hasClaudeAgents
      gitFlowConfig {
        enabled
        branches {
          stable
          development
          release
          feature
          bugfix
          hotfix
        }
        rules {
          feature { issueLabels baseBranch pipeline }
          bugfix { issueLabels baseBranch pipeline }
          hotfix { issueLabels baseBranch pipeline }
          branchNameOverride
        }
      }
    }
  }
`

export const UPDATE_REPOSITORY = gql`
  mutation UpdateRepository($name: String!, $input: UpdateRepositoryInput!) {
    updateRepository(name: $name, input: $input) {
      repository {
        name
        url
        branch
        pollers
        gitFlowConfig {
          enabled
          branches {
            stable
            development
            release
            feature
            bugfix
            hotfix
          }
          rules {
            feature { issueLabels baseBranch pipeline }
            bugfix { issueLabels baseBranch pipeline }
            hotfix { issueLabels baseBranch pipeline }
            branchNameOverride
          }
        }
      }
      errors {
        field
        message
      }
    }
  }
`

export const REGISTER_REPOSITORY = gql`
  mutation RegisterRepository($input: RegisterRepositoryInput!) {
    registerRepository(input: $input) {
      repository {
        name
        url
        branch
        pollers
        cloneStatus
        taskCount
      }
      errors {
        field
        message
      }
    }
  }
`

export const RETRY_CLONE = gql`
  mutation RetryClone($name: String!) {
    retryClone(name: $name) {
      repository {
        name
        cloneStatus
      }
      errors {
        field
        message
      }
    }
  }
`

export const REMOVE_REPOSITORY = gql`
  mutation RemoveRepository($name: String!) {
    removeRepository(name: $name) {
      repository {
        name
      }
      errors {
        field
        message
      }
    }
  }
`

// ── GitHub Auth ──────────────────────────────────────────────────────────────

export const GITHUB_AUTH_STATUS = gql`
  query GithubAuthStatus {
    githubAuthStatus {
      authenticated
      username
    }
  }
`

export const GITHUB_REPOSITORIES = gql`
  query GithubRepositories {
    githubRepositories {
      nameWithOwner
      url
      defaultBranch
      isPrivate
      description
    }
  }
`

export const GITHUB_BRANCHES = gql`
  query GithubBranches($owner: String!, $repo: String!) {
    githubBranches(owner: $owner, repo: $repo)
  }
`

export const GITHUB_LOGIN_START = gql`
  mutation GithubLoginStart {
    githubLoginStart {
      userCode
      verificationUri
      expiresIn
    }
  }
`

export const GITHUB_LOGIN_POLL = gql`
  mutation GithubLoginPoll {
    githubLoginPoll {
      success
      username
      error
    }
  }
`

export const GITHUB_LOGOUT = gql`
  mutation GithubLogout {
    githubLogout
  }
`

// ── Claude Auth ──────────────────────────────────────────────────────────────

export const CLAUDE_AUTH_STATUS = gql`
  query ClaudeAuthStatus {
    claudeAuthStatus {
      authenticated
      email
    }
  }
`

export const CLAUDE_LOGIN_START = gql`
  mutation ClaudeLoginStart {
    claudeLoginStart {
      authorizeUrl
      expiresIn
    }
  }
`

export const CLAUDE_LOGIN_POLL = gql`
  mutation ClaudeLoginPoll {
    claudeLoginPoll {
      success
      email
      error
    }
  }
`

export const CLAUDE_SUBMIT_CODE = gql`
  mutation ClaudeSubmitCode($code: String!) {
    claudeSubmitCode(code: $code) {
      success
      email
      error
    }
  }
`

export const CLAUDE_LOGOUT = gql`
  mutation ClaudeLogout {
    claudeLogout
  }
`
