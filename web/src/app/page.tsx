'use client'

import { useEffect, useReducer } from 'react'
import { useQuery } from '@apollo/client'
import { useRouter } from 'next/navigation'
import Box from '@mui/material/Box'
import Grid from '@mui/material/Grid'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
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
import Stack from '@mui/material/Stack'
import Divider from '@mui/material/Divider'
import { DASHBOARD_STATS, GET_TASKS, TOKEN_USAGE_BY_MODEL } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { TokenUsageChart } from '@/components/dashboard/TokenUsageChart'
import { formatDate, formatElapsed } from '@/lib/format'
import { formatCost, formatTokens } from '@/lib/spending'
import { monoStyle } from '@/lib/theme'

interface StatCardProps {
  label: string
  value: number | string | undefined
  color: string
  loading: boolean
}

function StatCard({ label, value, color, loading }: StatCardProps) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          {label}
        </Typography>
        {loading ? (
          <Skeleton variant="text" width={60} height={40} />
        ) : (
          <Typography variant="h4" fontWeight={700} sx={{ color }}>
            {value ?? 0}
          </Typography>
        )}
      </CardContent>
    </Card>
  )
}

interface TaskRow {
  id: string
  title: string
  status: string
  pipeline: string
  repository: { name: string }
  createdAt: string
  updatedAt: string
  completedAt?: string | null
  totalCostUsd?: number | null
  totalTokens?: number | null
}

/**
 * Self-ticking elapsed-time display.  Keeps its own 1 s interval so only
 * the individual cell re-renders — not the entire dashboard.
 */
function ElapsedTicker({ date }: { date: string }) {
  const [, tick] = useReducer((x: number) => x + 1, 0)
  useEffect(() => { const id = setInterval(tick, 1000); return () => clearInterval(id) }, [])
  return <>{formatElapsed(date)}</>
}

export default function DashboardPage() {
  const router = useRouter()

  const {
    data: statsData,
    loading: statsLoading,
    error: statsError,
  } = useQuery(DASHBOARD_STATS)

  const {
    data: tasksData,
    loading: tasksLoading,
    error: tasksError,
  } = useQuery(GET_TASKS, { variables: { limit: 10, offset: 0 } })

  const {
    data: tokenUsageData,
    loading: tokenUsageLoading,
  } = useQuery(TOKEN_USAGE_BY_MODEL, { variables: { days: 30 } })

  const stats = statsData?.dashboardStats

  const statCards = [
    { label: 'Total Tasks', value: stats?.totalTasks, color: '#1976d2' },
    { label: 'Pending', value: stats?.pendingTasks, color: '#757575' },
    { label: 'Executing', value: stats?.executingTasks, color: '#ed6c02' },
    { label: 'Completed', value: stats?.completedTasks, color: '#2e7d32' },
    { label: 'Failed', value: stats?.failedTasks, color: '#d32f2f' },
    { label: 'Blocked', value: stats?.blockedTasks, color: '#e65100' },
  ]

  return (
    <Box>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Dashboard
      </Typography>

      {statsError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load dashboard stats: {statsError.message}
        </Alert>
      )}

      {/* Stat cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {statCards.map((card) => (
          <Grid item xs={12} sm={6} md={4} lg={2} key={card.label}>
            <StatCard
              label={card.label}
              value={card.value}
              color={card.color}
              loading={statsLoading}
            />
          </Grid>
        ))}
      </Grid>

      {/* Token Usage Chart */}
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Token Usage (Last 30 Days)
          </Typography>
          <Divider sx={{ mb: 2 }} />
          <TokenUsageChart
            data={tokenUsageData?.tokenUsageByModel ?? []}
            loading={tokenUsageLoading}
          />
        </CardContent>
      </Card>

      {/* Tasks by Pipeline + Tasks by Repository */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} md={6}>
          <Card variant="outlined" sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={700} gutterBottom>
                Tasks by Pipeline
              </Typography>
              <Divider sx={{ mb: 1 }} />
              {statsLoading ? (
                <Stack spacing={1}>
                  {[...Array(4)].map((_, i) => (
                    <Skeleton key={i} variant="text" />
                  ))}
                </Stack>
              ) : stats?.tasksByPipeline?.length ? (
                <Stack spacing={0.5}>
                  {stats.tasksByPipeline.map(
                    (entry: { pipeline: string; count: number }) => (
                      <Box
                        key={entry.pipeline}
                        sx={{ display: 'flex', justifyContent: 'space-between', py: 0.5 }}
                      >
                        <Typography variant="body2">{entry.pipeline}</Typography>
                        <Typography variant="body2" fontWeight={600}>
                          {entry.count}
                        </Typography>
                      </Box>
                    )
                  )}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No data
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card variant="outlined" sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={700} gutterBottom>
                Tasks by Repository
              </Typography>
              <Divider sx={{ mb: 1 }} />
              {statsLoading ? (
                <Stack spacing={1}>
                  {[...Array(4)].map((_, i) => (
                    <Skeleton key={i} variant="text" />
                  ))}
                </Stack>
              ) : stats?.tasksByRepository?.length ? (
                <Stack spacing={0.5}>
                  {stats.tasksByRepository.map(
                    (entry: { repository: string; count: number }) => (
                      <Box
                        key={entry.repository}
                        sx={{ display: 'flex', justifyContent: 'space-between', py: 0.5 }}
                      >
                        <Typography variant="body2">{entry.repository}</Typography>
                        <Typography variant="body2" fontWeight={600}>
                          {entry.count}
                        </Typography>
                      </Box>
                    )
                  )}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No data
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Recent tasks */}
      <Typography variant="subtitle1" fontWeight={700} gutterBottom>
        Recent Tasks
      </Typography>

      {tasksError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load tasks: {tasksError.message}
        </Alert>
      )}

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Title</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Repository</TableCell>
              <TableCell>Pipeline</TableCell>
              <TableCell>Cost</TableCell>
              <TableCell>Updated</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {tasksLoading
              ? [...Array(5)].map((_, i) => (
                  <TableRow key={i}>
                    {[...Array(6)].map((_, j) => (
                      <TableCell key={j}>
                        <Skeleton variant="text" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : tasksData?.tasks?.nodes?.map((task: TaskRow) => (
                  <TableRow
                    key={task.id}
                    hover
                    sx={{ cursor: 'pointer' }}
                    onClick={() => router.push(`/tasks/${task.id}`)}
                    data-testid={`task-row-${task.id}`}
                  >
                    <TableCell>{task.title}</TableCell>
                    <TableCell>
                      <StatusChip status={task.status} />
                    </TableCell>
                    <TableCell>{task.repository.name}</TableCell>
                    <TableCell>{task.pipeline ?? '—'}</TableCell>
                    <TableCell>
                      <Typography variant="body2" color="warning.main" sx={{ ...monoStyle, fontSize: '0.8rem' }}>
                        {formatCost(task.totalCostUsd)}
                      </Typography>
                      {task.totalTokens != null && task.totalTokens > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ ...monoStyle, fontSize: '0.7rem', display: 'block' }}>
                          {formatTokens(task.totalTokens)}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell title={formatDate(task.updatedAt)}>
                      {['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED'].includes(task.status?.toUpperCase())
                        ? formatDate(task.completedAt || task.updatedAt)
                        : <ElapsedTicker date={task.updatedAt} />}
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}
