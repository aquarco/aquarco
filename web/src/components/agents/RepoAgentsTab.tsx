'use client'

import React, { useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import Box from '@mui/material/Box'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import CircularProgress from '@mui/material/CircularProgress'
import Skeleton from '@mui/material/Skeleton'
import Snackbar from '@mui/material/Snackbar'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import GitHubIcon from '@mui/icons-material/GitHub'
import FolderIcon from '@mui/icons-material/Folder'
import AgentTable, { type AgentDefinitionRow } from './AgentTable'
import AgentEditDialog from './AgentEditDialog'
import {
  GET_REPO_AGENT_GROUPS,
  SET_AGENT_DISABLED,
  RESET_AGENT_MODIFICATION,
  CREATE_AGENT_PR,
} from '@/lib/graphql/queries'

interface RepoAgentGroup {
  repoName: string
  agents: AgentDefinitionRow[]
}

export default function RepoAgentsTab() {
  const { data, loading, error, refetch } = useQuery(GET_REPO_AGENT_GROUPS)
  const [setAgentDisabled] = useMutation(SET_AGENT_DISABLED)
  const [resetAgentModification] = useMutation(RESET_AGENT_MODIFICATION)
  const [createAgentPR, { loading: prLoading }] = useMutation(CREATE_AGENT_PR)

  const [editAgent, setEditAgent] = useState<AgentDefinitionRow | null>(null)
  const [editScope, setEditScope] = useState<string>('global')
  const [snackbar, setSnackbar] = useState<{ message: string; severity: 'success' | 'error' } | null>(null)

  const groups: RepoAgentGroup[] = data?.repoAgentGroups ?? []

  async function handleToggleDisabled(agent: AgentDefinitionRow, repoName: string) {
    const scope = `repo:${repoName}`
    try {
      await setAgentDisabled({
        variables: {
          name: agent.name,
          scope,
          disabled: !agent.isDisabled,
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

  async function handleReset(agent: AgentDefinitionRow, repoName: string) {
    const scope = `repo:${repoName}`
    try {
      await resetAgentModification({
        variables: { name: agent.name, scope },
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

  if (error) {
    return (
      <Alert severity="error" sx={{ mb: 2 }}>
        Failed to load repository agents: {error.message}
      </Alert>
    )
  }

  if (loading) {
    return (
      <Stack spacing={1}>
        {[...Array(3)].map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={56} sx={{ borderRadius: 1 }} />
        ))}
      </Stack>
    )
  }

  if (groups.length === 0) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography variant="body1" color="text.secondary">
          No repositories with custom agents found.
        </Typography>
      </Box>
    )
  }

  return (
    <Box>
      {groups.map((group) => {
        const hasModified = group.agents.some((a) => a.isModified)
        return (
          <Accordion key={group.repoName} defaultExpanded={groups.length <= 3}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <FolderIcon color="action" fontSize="small" />
                <Typography fontWeight={600}>{group.repoName}</Typography>
                <Typography variant="body2" color="text.secondary">
                  ({group.agents.length} agent{group.agents.length !== 1 ? 's' : ''})
                </Typography>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <AgentTable
                agents={group.agents}
                showSource={false}
                onToggleDisabled={(agent) => handleToggleDisabled(agent, group.repoName)}
                onEdit={(agent) => {
                  setEditAgent(agent)
                  setEditScope(`repo:${group.repoName}`)
                }}
                onReset={(agent) => handleReset(agent, group.repoName)}
              />
              {hasModified && (
                <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1 }}>
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={prLoading ? <CircularProgress size={16} /> : <GitHubIcon />}
                    onClick={() => handleCreatePR(group.repoName)}
                    disabled={prLoading}
                    data-testid={`btn-create-pr-${group.repoName}`}
                  >
                    Create PR to {group.repoName}
                  </Button>
                </Stack>
              )}
            </AccordionDetails>
          </Accordion>
        )
      })}

      <AgentEditDialog
        agent={editAgent}
        scope={editScope}
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
