'use client'

import React, { useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import Box from '@mui/material/Box'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import CircularProgress from '@mui/material/CircularProgress'
import Snackbar from '@mui/material/Snackbar'
import GitHubIcon from '@mui/icons-material/GitHub'
import AgentTable, { type AgentDefinitionRow } from './AgentTable'
import AgentEditDialog from './AgentEditDialog'
import {
  GET_GLOBAL_AGENTS,
  SET_AGENT_DISABLED,
  RESET_AGENT_MODIFICATION,
  CREATE_AGENT_PR,
} from '@/lib/graphql/queries'

export default function GlobalAgentsTab() {
  const { data, loading, error, refetch } = useQuery(GET_GLOBAL_AGENTS)
  const [setAgentDisabled] = useMutation(SET_AGENT_DISABLED)
  const [resetAgentModification] = useMutation(RESET_AGENT_MODIFICATION)
  const [createAgentPR, { loading: prLoading }] = useMutation(CREATE_AGENT_PR)

  const [editAgent, setEditAgent] = useState<AgentDefinitionRow | null>(null)
  const [snackbar, setSnackbar] = useState<{ message: string; severity: 'success' | 'error' } | null>(null)

  const agents: AgentDefinitionRow[] = data?.globalAgents ?? []

  // Find if there are any global config repos with modifications
  const hasModifiedAgents = agents.some((a) => a.isModified)
  const configRepoNames = [...new Set(
    agents
      .filter((a) => a.source === 'GLOBAL_CONFIG' && a.sourceRepo)
      .map((a) => a.sourceRepo!)
  )]

  async function handleToggleDisabled(agent: AgentDefinitionRow) {
    try {
      await setAgentDisabled({
        variables: {
          name: agent.name,
          scope: 'global',
          disabled: !agent.isDisabled,
        },
        optimisticResponse: {
          setAgentDisabled: {
            __typename: 'AgentDefinitionPayload',
            agent: {
              __typename: 'AgentDefinition',
              name: agent.name,
              isDisabled: !agent.isDisabled,
            },
            errors: [],
          },
        },
      })
      refetch()
    } catch (err) {
      setSnackbar({
        message: err instanceof Error ? err.message : 'Failed to update agent',
        severity: 'error',
      })
    }
  }

  async function handleReset(agent: AgentDefinitionRow) {
    try {
      await resetAgentModification({
        variables: { name: agent.name, scope: 'global' },
      })
      refetch()
      setSnackbar({ message: `Reset ${agent.name} to original spec`, severity: 'success' })
    } catch (err) {
      setSnackbar({
        message: err instanceof Error ? err.message : 'Failed to reset agent',
        severity: 'error',
      })
    }
  }

  async function handleCreatePR(repoName: string) {
    try {
      const result = await createAgentPR({ variables: { repoName } })
      const errors = result.data?.createAgentPR?.errors
      if (errors && errors.length > 0) {
        setSnackbar({ message: errors[0].message, severity: 'error' })
        return
      }
      const prUrl = result.data?.createAgentPR?.prUrl
      setSnackbar({ message: `PR created: ${prUrl}`, severity: 'success' })
    } catch (err) {
      setSnackbar({
        message: err instanceof Error ? err.message : 'Failed to create PR',
        severity: 'error',
      })
    }
  }

  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load global agents: {error.message}
        </Alert>
      )}

      <AgentTable
        agents={agents}
        loading={loading}
        showSource={true}
        onToggleDisabled={handleToggleDisabled}
        onEdit={(agent) => setEditAgent(agent)}
        onReset={handleReset}
      />

      {hasModifiedAgents && configRepoNames.length > 0 && (
        <Stack direction="row" spacing={1} sx={{ mt: 2 }} justifyContent="flex-end">
          {configRepoNames.map((repoName) => (
            <Button
              key={repoName}
              variant="outlined"
              startIcon={prLoading ? <CircularProgress size={18} /> : <GitHubIcon />}
              onClick={() => handleCreatePR(repoName)}
              disabled={prLoading}
              data-testid={`btn-create-pr-${repoName}`}
            >
              Create PR to {repoName}
            </Button>
          ))}
        </Stack>
      )}

      <AgentEditDialog
        agent={editAgent}
        scope="global"
        open={editAgent !== null}
        onClose={() => setEditAgent(null)}
        onSaved={() => refetch()}
      />

      <Snackbar
        open={snackbar !== null}
        autoHideDuration={6000}
        onClose={() => setSnackbar(null)}
        message={snackbar?.message}
      />
    </Box>
  )
}
