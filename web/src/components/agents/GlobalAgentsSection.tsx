'use client'

import React from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Stack from '@mui/material/Stack'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import AgentCard from './AgentCard'
import type { AgentDefinition } from './AgentCard'

interface GlobalAgentsSectionProps {
  agents: AgentDefinition[]
  loading: boolean
  onToggleDisabled: (agentName: string, isDisabled: boolean) => void
  onEdit: (agent: AgentDefinition) => void
  onReset: (agent: AgentDefinition) => void
}

export default function GlobalAgentsSection({
  agents,
  loading,
  onToggleDisabled,
  onEdit,
  onReset,
}: GlobalAgentsSectionProps) {
  const defaultAgents = agents.filter((a) => a.source === 'DEFAULT')
  const globalConfigAgents = agents.filter((a) => a.source === 'GLOBAL')

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="h6" fontWeight={600} sx={{ mb: 2 }}>
        Global Agents
      </Typography>

      {loading ? (
        <Stack spacing={1}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} variant="rounded" height={72} />
          ))}
        </Stack>
      ) : (
        <>
          {defaultAgents.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
                Default Agents ({defaultAgents.length})
              </Typography>
              <Stack spacing={1}>
                {defaultAgents.map((agent) => (
                  <AgentCard
                    key={agent.name}
                    agent={agent}
                    onToggleDisabled={onToggleDisabled}
                    onEdit={onEdit}
                    onReset={onReset}
                    disableEdit
                  />
                ))}
              </Stack>
            </Box>
          )}

          {globalConfigAgents.length > 0 && (
            <Box>
              <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
                Global Config Agents ({globalConfigAgents.length})
              </Typography>
              <Stack spacing={1}>
                {globalConfigAgents.map((agent) => (
                  <AgentCard
                    key={agent.name}
                    agent={agent}
                    onToggleDisabled={onToggleDisabled}
                    onEdit={onEdit}
                    onReset={onReset}
                  />
                ))}
              </Stack>
            </Box>
          )}

          {defaultAgents.length === 0 && globalConfigAgents.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No global agents configured.
            </Typography>
          )}
        </>
      )}
    </Paper>
  )
}
