import { gql } from '@apollo/client'

// ── Task Queries ──────────────────────────────────────────────────────────────

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
        totalCostUsd
        totalTokens
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
      lastCompletedStageId
      checkpointData
      pipelineVersion
      retryCount
      errorMessage
      parentTaskId
      prNumber
      branchName
      stages {
        id
        stageNumber
        iteration
        run
        executionOrder
        category
        agent
        agentVersion
        status
        startedAt
        completedAt
        structuredOutput
        tokensInput
        tokensOutput
        costUsd
        cacheReadTokens
        cacheWriteTokens
        errorMessage
        retryCount
        liveOutput
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

// ── Task Mutations ────────────────────────────────────────────────────────────

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

export const CONTINUE_TASK = gql`
  mutation ContinueTask($id: ID!) {
    continueTask(id: $id) {
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

export const RERUN_TASK = gql`
  mutation RerunTask($id: ID!) {
    rerunTask(id: $id) {
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

export const CLOSE_TASK = gql`
  mutation CloseTask($id: ID!) {
    closeTask(id: $id) {
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
