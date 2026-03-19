'use client'

import { useState, useEffect, useReducer } from 'react'
import { useQuery } from '@apollo/client'
import { useRouter } from 'next/navigation'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import TablePagination from '@mui/material/TablePagination'
import Paper from '@mui/material/Paper'
import Skeleton from '@mui/material/Skeleton'
import Alert from '@mui/material/Alert'
import Stack from '@mui/material/Stack'
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import Button from '@mui/material/Button'
import AddIcon from '@mui/icons-material/Add'
import { GET_TASKS, GET_REPOSITORIES } from '@/lib/graphql/queries'
import { StatusChip } from '@/components/ui/StatusChip'
import { CreateTaskDialog } from '@/components/tasks/CreateTaskDialog'
import { monoStyle } from '@/lib/theme'
import { formatDate, formatElapsed } from '@/lib/format'

const TASK_STATUSES = ['PENDING', 'QUEUED', 'EXECUTING', 'COMPLETED', 'FAILED', 'TIMEOUT', 'BLOCKED']

interface Task {
  id: string
  title: string
  status: string
  repository: { name: string }
  createdAt: string
  updatedAt: string
  pipeline?: string | null
  assignedAgent?: string | null
}

interface Repository {
  name: string
}

export default function TasksPage() {
  const router = useRouter()
  const [statusFilter, setStatusFilter] = useState('')
  const [repoFilter, setRepoFilter] = useState('')
  const [page, setPage] = useState(0)
  const [rowsPerPage, setRowsPerPage] = useState(25)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [, tick] = useReducer((x: number) => x + 1, 0)
  useEffect(() => { const id = setInterval(tick, 1000); return () => clearInterval(id) }, [])

  const { data: reposData } = useQuery(GET_REPOSITORIES)

  const { data, loading, error, refetch } = useQuery(GET_TASKS, {
    variables: {
      limit: rowsPerPage,
      offset: page * rowsPerPage,
      status: statusFilter || undefined,
      repository: repoFilter || undefined,
    },
    pollInterval: 5000,
  })

  const tasks: Task[] = data?.tasks?.nodes ?? []
  const totalCount: number = data?.tasks?.totalCount ?? -1
  const repositories: Repository[] = reposData?.repositories ?? []

  const handleTaskCreated = () => {
    refetch()
  }

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5" fontWeight={700}>
          Tasks
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setDialogOpen(true)}
          data-testid="btn-create-task"
        >
          Create Task
        </Button>
      </Stack>

      {/* Filters */}
      <Stack direction="row" spacing={2} sx={{ mb: 2 }} flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Status</InputLabel>
          <Select
            value={statusFilter}
            label="Status"
            onChange={(e) => { setStatusFilter(e.target.value); setPage(0) }}
            data-testid="filter-status"
          >
            <MenuItem value="">All</MenuItem>
            {TASK_STATUSES.map((s) => (
              <MenuItem key={s} value={s}>{s}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel>Repository</InputLabel>
          <Select
            value={repoFilter}
            label="Repository"
            onChange={(e) => { setRepoFilter(e.target.value); setPage(0) }}
            data-testid="filter-repository"
          >
            <MenuItem value="">All</MenuItem>
            {repositories.map((r) => (
              <MenuItem key={r.name} value={r.name}>{r.name}</MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load tasks: {error.message}
        </Alert>
      )}

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>ID</TableCell>
              <TableCell>Title</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Repository</TableCell>
              <TableCell>Pipeline</TableCell>
              <TableCell>Updated</TableCell>
              <TableCell>Agent</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading
              ? [...Array(rowsPerPage > 10 ? 10 : rowsPerPage)].map((_, i) => (
                  <TableRow key={i}>
                    {[...Array(7)].map((_, j) => (
                      <TableCell key={j}>
                        <Skeleton variant="text" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : tasks.map((task) => (
                  <TableRow
                    key={task.id}
                    hover
                    sx={{ cursor: 'pointer' }}
                    onClick={() => router.push(`/tasks/${task.id}`)}
                    data-testid={`task-row-${task.id}`}
                  >
                    <TableCell>
                      <Typography sx={monoStyle} component="span">
                        {task.id.slice(0, 8)}
                      </Typography>
                    </TableCell>
                    <TableCell>{task.title}</TableCell>
                    <TableCell>
                      <StatusChip status={task.status} />
                    </TableCell>
                    <TableCell>{task.repository.name}</TableCell>
                    <TableCell>{task.pipeline ?? '—'}</TableCell>
                    <TableCell title={formatDate(task.updatedAt)}>
                      {['COMPLETED', 'FAILED', 'TIMEOUT'].includes(task.status?.toUpperCase())
                        ? formatDate(task.completedAt || task.updatedAt)
                        : formatElapsed(task.updatedAt)}
                    </TableCell>
                    <TableCell>{task.assignedAgent ?? '—'}</TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>

      <TablePagination
        component="div"
        count={totalCount}
        rowsPerPage={rowsPerPage}
        page={page}
        onPageChange={(_, newPage) => setPage(newPage)}
        onRowsPerPageChange={(e) => {
          setRowsPerPage(parseInt(e.target.value, 10))
          setPage(0)
        }}
        rowsPerPageOptions={[10, 25, 50]}
        labelDisplayedRows={({ from, to }) => `${from}–${to}`}
      />

      <CreateTaskDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSuccess={handleTaskCreated}
        repositories={repositories}
      />
    </Box>
  )
}
