'use client'

import React, { useState } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Stack from '@mui/material/Stack'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import Chip from '@mui/material/Chip'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import FolderIcon from '@mui/icons-material/Folder'
import AgentCard from './AgentCard'
import type { AgentDefinition } from './AgentCard'

interface RepositoryWithAgents {
  repository: {
    name: string
    url: string
    branch: string | null
    isConfigRepo: boolean
    cloneStatus: string
  }
  agents: AgentDefinition[]
}

interface RepositoryAgentsSectionProps {
  repositoriesWithAgents: RepositoryWithAgents[]
  loading: boolean
  onToggleDisabled: (agentName: string, isDisabled: boolean, scopeRepository: string) => void
  onEdit: (agent: AgentDefinition) => void
  onReset: (agent: AgentDefinition) => void
}

export default function RepositoryAgentsSection({
  repositoriesWithAgents,
  loading,
  onToggleDisabled,
  onEdit,
  onReset,
}: RepositoryAgentsSectionProps) {
  const [expanded, setExpanded] = useState<string | false>(false)

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="h6" fontWeight={600} sx={{ mb: 2 }}>
        Repository Agents
      </Typography>

      {loading ? (
        <Stack spacing={1}>
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} variant="rounded" height={56} />
          ))}
        </Stack>
      ) : repositoriesWithAgents.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No repositories with custom agents found.
        </Typography>
      ) : (
        <Stack spacing={1}>
          {repositoriesWithAgents.map(({ repository, agents }) => (
            <Accordion
              key={repository.name}
              expanded={expanded === repository.name}
              onChange={(_, isExpanded) => setExpanded(isExpanded ? repository.name : false)}
              variant="outlined"
              disableGutters
              sx={{ '&:before': { display: 'none' } }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Stack direction="row" alignItems="center" spacing={1.5}>
                  <FolderIcon fontSize="small" color="action" />
                  <Typography variant="subtitle2" fontWeight={600}>
                    {repository.name}
                  </Typography>
                  <Chip
                    label={`${agents.length} agent${agents.length !== 1 ? 's' : ''}`}
                    size="small"
                    variant="outlined"
                    sx={{ height: 20, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
                  />
                  {agents.some((a) => a.isDisabled) && (
                    <Chip
                      label={`${agents.filter((a) => a.isDisabled).length} disabled`}
                      size="small"
                      color="warning"
                      variant="outlined"
                      sx={{ height: 20, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
                    />
                  )}
                </Stack>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={1}>
                  {agents.map((agent) => (
                    <AgentCard
                      key={agent.name}
                      agent={agent}
                      onToggleDisabled={(name, disabled) =>
                        onToggleDisabled(name, disabled, repository.name)
                      }
                      onEdit={onEdit}
                      onReset={onReset}
                    />
                  ))}
                </Stack>
              </AccordionDetails>
            </Accordion>
          ))}
        </Stack>
      )}
    </Paper>
  )
}
