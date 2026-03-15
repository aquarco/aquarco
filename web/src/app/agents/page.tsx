'use client'

import { useQuery } from '@apollo/client'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import { GET_AGENT_INSTANCES } from '@/lib/graphql/queries'
import { formatDate, formatNumber } from '@/lib/format'

interface AgentInstance {
  agentName: string
  activeCount: number
  totalExecutions: number
  totalTokensUsed: number
  lastExecutionAt: string | null
}

interface ActiveDotProps {
  active: boolean
}

function ActiveDot({ active }: ActiveDotProps) {
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

export default function AgentsPage() {
  const { data, loading, error } = useQuery(GET_AGENT_INSTANCES)

  const agents: AgentInstance[] = (data?.agentInstances ?? []).slice().sort(
    (a: AgentInstance, b: AgentInstance) => a.agentName.localeCompare(b.agentName)
  )

  return (
    <Box>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Agents
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load agents: {error.message}
        </Alert>
      )}

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Agent Name</TableCell>
              <TableCell align="right">Active Instances</TableCell>
              <TableCell align="right">Total Executions</TableCell>
              <TableCell align="right">Total Tokens Used</TableCell>
              <TableCell>Last Execution</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading
              ? [...Array(6)].map((_, i) => (
                  <TableRow key={i}>
                    {[...Array(5)].map((_, j) => (
                      <TableCell key={j}>
                        <Skeleton variant="text" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : agents.map((agent) => (
                  <TableRow key={agent.agentName} data-testid={`agent-row-${agent.agentName}`}>
                    <TableCell>
                      <ActiveDot active={agent.activeCount > 0} />
                      {agent.agentName}
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
                    <TableCell>{formatDate(agent.lastExecutionAt)}</TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}
