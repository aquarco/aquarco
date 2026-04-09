import { gql } from '@apollo/client'

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
      group
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

// ── Pipeline Definitions ─────────────────────────────────────────────────────

export const GET_PIPELINE_DEFINITIONS = gql`
  query GetPipelineDefinitions {
    pipelineDefinitions {
      name
      version
      categories
      stages {
        name
        category
        required
        conditions {
          type
          expression
          onYes
          onNo
          maxRepeats
        }
      }
    }
  }
`
