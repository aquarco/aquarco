'use client'

import React from 'react'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Switch from '@mui/material/Switch'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import Tooltip from '@mui/material/Tooltip'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import EditIcon from '@mui/icons-material/Edit'
import RestoreIcon from '@mui/icons-material/Restore'
import { formatDate, formatNumber } from '@/lib/format'

export interface AgentDefinitionRow {
  name: string
  version: string
  description: string
  source: 'DEFAULT' | 'GLOBAL_CONFIG' | 'REPOSITORY'
  sourceRepo: string | null
  spec: unknown
  isDisabled: boolean
  isModified: boolean
  modifiedSpec: unknown
  activeCount: number
  totalExecutions: number
  totalTokensUsed: number
  lastExecutionAt: string | null
}

interface AgentTableProps {
  agents: AgentDefinitionRow[]
  loading?: boolean
  showSource?: boolean
  onToggleDisabled: (agent: AgentDefinitionRow) => void
  onEdit: (agent: AgentDefinitionRow) => void
  onReset: (agent: AgentDefinitionRow) => void
}

function ActiveDot({ active }: { active: boolean }) {
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        backgroundColor: active ? 'success.main' : 'grey.400',
        mr: 1,
        verticalAlign: 'middle',
      }}
    />
  )
}

function SourceChip({ source, sourceRepo }: { source: string; sourceRepo: string | null }) {
  if (source === 'DEFAULT') {
    return <Chip label="Default" size="small" color="default" variant="outlined" />
  }
  if (source === 'GLOBAL_CONFIG') {
    return <Chip label={sourceRepo ?? 'Global'} size="small" color="primary" variant="outlined" />
  }
  return <Chip label={sourceRepo ?? 'Repo'} size="small" color="secondary" variant="outlined" />
}

export default function AgentTable({
  agents,
  loading,
  showSource = true,
  onToggleDisabled,
  onEdit,
  onReset,
}: AgentTableProps) {
  if (loading) {
    return (
      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell width={60}>Enabled</TableCell>
              <TableCell>Agent Name</TableCell>
              {showSource && <TableCell>Source</TableCell>}
              <TableCell>Description</TableCell>
              <TableCell align="right">Active</TableCell>
              <TableCell align="right">Executions</TableCell>
              <TableCell align="right">Tokens</TableCell>
              <TableCell width={100}>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {[...Array(6)].map((_, i) => (
              <TableRow key={i}>
                {[...Array(showSource ? 8 : 7)].map((_, j) => (
                  <TableCell key={j}>
                    <Skeleton variant="text" />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    )
  }

  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell width={60}>Enabled</TableCell>
            <TableCell>Agent Name</TableCell>
            {showSource && <TableCell>Source</TableCell>}
            <TableCell>Description</TableCell>
            <TableCell align="right">Active</TableCell>
            <TableCell align="right">Executions</TableCell>
            <TableCell align="right">Tokens</TableCell>
            <TableCell width={100}>Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {agents.length === 0 ? (
            <TableRow>
              <TableCell colSpan={showSource ? 8 : 7} align="center">
                <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
                  No agents found
                </Typography>
              </TableCell>
            </TableRow>
          ) : (
            agents.map((agent) => (
              <TableRow
                key={agent.name}
                data-testid={`agent-row-${agent.name}`}
                sx={{ opacity: agent.isDisabled ? 0.5 : 1 }}
              >
                <TableCell>
                  <Switch
                    checked={!agent.isDisabled}
                    onChange={() => onToggleDisabled(agent)}
                    size="small"
                    data-testid={`agent-toggle-${agent.name}`}
                  />
                </TableCell>
                <TableCell>
                  <ActiveDot active={agent.activeCount > 0} />
                  {agent.name}
                  {agent.isModified && (
                    <Chip
                      label="modified"
                      size="small"
                      color="warning"
                      variant="outlined"
                      sx={{ ml: 1 }}
                    />
                  )}
                </TableCell>
                {showSource && (
                  <TableCell>
                    <SourceChip source={agent.source} sourceRepo={agent.sourceRepo} />
                  </TableCell>
                )}
                <TableCell>
                  <Typography variant="body2" noWrap sx={{ maxWidth: 300 }}>
                    {agent.description}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <Typography
                    variant="body2"
                    fontWeight={agent.activeCount > 0 ? 700 : 400}
                    color={agent.activeCount > 0 ? 'success.main' : 'text.primary'}
                  >
                    {agent.activeCount}
                  </Typography>
                </TableCell>
                <TableCell align="right">{formatNumber(agent.totalExecutions)}</TableCell>
                <TableCell align="right">{formatNumber(agent.totalTokensUsed)}</TableCell>
                <TableCell>
                  {agent.source !== 'DEFAULT' && (
                    <Tooltip title="Edit agent spec">
                      <IconButton
                        size="small"
                        onClick={() => onEdit(agent)}
                        data-testid={`agent-edit-${agent.name}`}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                  {agent.isModified && (
                    <Tooltip title="Reset to original">
                      <IconButton
                        size="small"
                        onClick={() => onReset(agent)}
                        data-testid={`agent-reset-${agent.name}`}
                      >
                        <RestoreIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </TableContainer>
  )
}
