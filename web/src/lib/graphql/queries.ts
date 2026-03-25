import { gql } from '@apollo/client'

// ── Dashboard ─────────────────────────────────────────────────────────────────

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
      tasksByPipeline {
        pipeline
        count
      }
      tasksByRepository {
        repository
        count
      }
    }
  }
`

// ── Tasks ─────────────────────────────────────────────────────────────────────

export const GET_TASKS = gql`
  query GetTasks(
    $limit: Int
    $offset: Int
    $status: TaskStatus
    $repository: String
  ) {
    tasks(
      limit: $limit
      offset: $offset
      status: $status
      repository: $repository
    ) {
      nodes {
        id
        title
        status
        repository {
          name
        }
        createdAt
        updatedAt
        pipeline
        assignedAgent
      }
      totalCount
    }
  }
`

export const GET_TASK = gql`
  query GetTask($id: ID!) {
    task(id: $id) {
      id
      title
      status
      priority
      source
      sourceRef
      pipeline
      repository {
        name
      }
      createdAt
      updatedAt
      startedAt
      completedAt
      assignedAgent
      currentStage
      retryCount
      errorMessage
      stages {
        id
        stageNumber
        category
        agent
        agentVersion
        status
        startedAt
        completedAt
        structuredOutput
        rawOutput
        tokensInput
        tokensOutput
        errorMessage
        retryCount
      }
      context {
        id
        key
        valueType
        valueJson
        valueText
        valueFileRef
        createdAt
        stageNumber
      }
    }
  }
`

// ── Repositories ──────────────────────────────────────────────────────────────

export const GET_REPOSITORIES = gql`
  query GetRepositories {
    repositories {
      name
      url
      branch
      cloneDir
      pollers
      isConfigRepo
      lastClonedAt
      lastPulledAt
      cloneStatus
      headSha
      errorMessage
      deployPublicKey
      taskCount
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
        isConfigRepo
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

export const SET_CONFIG_REPO = gql`
  mutation SetConfigRepo($name: String!, $isConfigRepo: Boolean!) {
    setConfigRepo(name: $name, isConfigRepo: $isConfigRepo) {
      repository {
        name
        isConfigRepo
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

// ── Agents ────────────────────────────────────────────────────────────────────

export const GET_AGENT_INSTANCES = gql`
  query GetAgentInstances {
    agentInstances {
      agentName
      activeCount
      totalExecutions
      totalTokensUsed
      lastExecutionAt
    }
  }
`

export const GET_GLOBAL_AGENTS = gql`
  query GetGlobalAgents {
    globalAgents {
      name
      version
      description
      source
      sourceRepo
      spec
      isDisabled
      isModified
      modifiedSpec
      activeCount
      totalExecutions
      totalTokensUsed
      lastExecutionAt
    }
  }
`

export const GET_REPO_AGENT_GROUPS = gql`
  query GetRepoAgentGroups {
    repoAgentGroups {
      repoName
      agents {
        name
        version
        description
        source
        sourceRepo
        spec
        isDisabled
        isModified
        modifiedSpec
        activeCount
        totalExecutions
        totalTokensUsed
        lastExecutionAt
      }
    }
  }
`

export const SET_AGENT_DISABLED = gql`
  mutation SetAgentDisabled($name: String!, $scope: String!, $disabled: Boolean!) {
    setAgentDisabled(name: $name, scope: $scope, disabled: $disabled) {
      agent {
        name
        isDisabled
      }
      errors {
        field
        message
      }
    }
  }
`

export const MODIFY_AGENT = gql`
  mutation ModifyAgent($name: String!, $scope: String!, $spec: JSON!) {
    modifyAgent(name: $name, scope: $scope, spec: $spec) {
      agent {
        name
        spec
        isModified
        modifiedSpec
      }
      errors {
        field
        message
      }
    }
  }
`

export const RESET_AGENT_MODIFICATION = gql`
  mutation ResetAgentModification($name: String!, $scope: String!) {
    resetAgentModification(name: $name, scope: $scope) {
      agent {
        name
        spec
        isModified
        modifiedSpec
      }
      errors {
        field
        message
      }
    }
  }
`

export const CREATE_AGENT_PR = gql`
  mutation CreateAgentPR($repoName: String!) {
    createAgentPR(repoName: $repoName) {
      prUrl
      errors {
        field
        message
      }
    }
  }
`

// ── Task mutations ────────────────────────────────────────────────────────────

export const CREATE_TASK = gql`
  mutation CreateTask($input: CreateTaskInput!) {
    createTask(input: $input) {
      task {
        id
        title
        status
        pipeline
        createdAt
      }
      errors {
        field
        message
      }
    }
  }
`

export const UPDATE_TASK_STATUS = gql`
  mutation UpdateTaskStatus($id: ID!, $status: TaskStatus!) {
    updateTaskStatus(id: $id, status: $status) {
      task {
        id
        status
        updatedAt
      }
      errors {
        field
        message
      }
    }
  }
`

export const RETRY_TASK = gql`
  mutation RetryTask($id: ID!) {
    retryTask(id: $id) {
      task {
        id
        status
        updatedAt
      }
      errors {
        field
        message
      }
    }
  }
`

export const CANCEL_TASK = gql`
  mutation CancelTask($id: ID!) {
    cancelTask(id: $id) {
      task {
        id
        status
        updatedAt
      }
      errors {
        field
        message
      }
    }
  }
`

export const UNBLOCK_TASK = gql`
  mutation UnblockTask($id: ID!, $resolution: String!) {
    unblockTask(id: $id, resolution: $resolution) {
      task {
        id
        status
        updatedAt
      }
      errors {
        field
        message
      }
    }
  }
`
