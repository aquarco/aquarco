'use client'

import React from 'react'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Switch from '@mui/material/Switch'
import IconButton from '@mui/material/IconButton'
import Tooltip from '@mui/material/Tooltip'
import EditIcon from '@mui/icons-material/Edit'
import RestoreIcon from '@mui/icons-material/Restore'

export interface AgentDefinition {
  name: string
  version: string
  description: string | null
  source: 'DEFAULT' | 'GLOBAL' | 'REPOSITORY'
  sourceRepository: string | null
  spec: Record<string, unknown>
  labels: Record<string, string> | null
  isActive: boolean
  isDisabled: boolean
  hasOverride: boolean
  modifiedSpec: Record<string, unknown> | null
}

interface AgentCardProps {
  agent: AgentDefinition
  onToggleDisabled: (agentName: string, isDisabled: boolean) => void
  onEdit: (agent: AgentDefinition) => void
  onReset: (agent: AgentDefinition) => void
  disableEdit?: boolean
}

const sourceColors: Record<string, 'default' | 'primary' | 'secondary'> = {
  DEFAULT: 'default',
  GLOBAL: 'primary',
  REPOSITORY: 'secondary',
}

const sourceLabels: Record<string, string> = {
  DEFAULT: 'Default',
  GLOBAL: 'Global Config',
  REPOSITORY: 'Repository',
}

export default function AgentCard({
  agent,
  onToggleDisabled,
  onEdit,
  onReset,
  disableEdit = false,
}: AgentCardProps) {
  const spec = agent.modifiedSpec ?? agent.spec
  const tools = (spec as Record<string, unknown>)?.tools as Record<string, unknown> | undefined
  const resources = (spec as Record<string, unknown>)?.resources as Record<string, unknown> | undefined

  return (
    <Card
      variant="outlined"
      sx={{
        opacity: agent.isDisabled ? 0.6 : 1,
        borderColor: agent.hasOverride ? 'warning.main' : undefined,
      }}
    >
      <CardContent sx={{ py: 1.5, px: 2, '&:last-child': { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="subtitle2" noWrap sx={{ fontWeight: 600 }}>
              {agent.name}
            </Typography>
            <Chip
              label={sourceLabels[agent.source]}
              color={sourceColors[agent.source]}
              size="small"
              variant="outlined"
              sx={{ height: 20, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
            />
            {agent.hasOverride && (
              <Chip
                label="Modified"
                color="warning"
                size="small"
                sx={{ height: 20, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
              />
            )}
            <Typography variant="caption" color="text.secondary" noWrap>
              v{agent.version}
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            {agent.hasOverride && (
              <Tooltip title="Reset to original">
                <IconButton size="small" onClick={() => onReset(agent)}>
                  <RestoreIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {!disableEdit && (
              <Tooltip title="Edit agent spec">
                <IconButton size="small" onClick={() => onEdit(agent)} disabled={agent.source === 'DEFAULT'}>
                  <EditIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title={agent.isDisabled ? 'Enable agent' : 'Disable agent'}>
              <Switch
                size="small"
                checked={!agent.isDisabled}
                onChange={() => onToggleDisabled(agent.name, !agent.isDisabled)}
              />
            </Tooltip>
          </Stack>
        </Stack>
        {agent.description && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }} noWrap>
            {agent.description}
          </Typography>
        )}
        <Stack direction="row" spacing={2} sx={{ mt: 0.5 }}>
          {resources?.timeoutMinutes && (
            <Typography variant="caption" color="text.secondary">
              Timeout: {String(resources.timeoutMinutes)}m
            </Typography>
          )}
          {tools?.allowed && Array.isArray(tools.allowed) && (
            <Typography variant="caption" color="text.secondary">
              Tools: {tools.allowed.length} allowed
            </Typography>
          )}
          {agent.sourceRepository && (
            <Typography variant="caption" color="text.secondary">
              Repo: {agent.sourceRepository}
            </Typography>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
