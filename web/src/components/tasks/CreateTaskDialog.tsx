'use client'

import { useState, useCallback } from 'react'
import { useMutation } from '@apollo/client'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import TextField from '@mui/material/TextField'
import Stack from '@mui/material/Stack'
import Alert from '@mui/material/Alert'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import Slider from '@mui/material/Slider'
import Typography from '@mui/material/Typography'
import Box from '@mui/material/Box'
import { CREATE_TASK } from '@/lib/graphql/queries'

const PIPELINES = [
  { value: 'feature-pipeline', label: 'Feature Pipeline' },
  { value: 'bugfix-pipeline', label: 'Bugfix Pipeline' },
  { value: 'pr-review-pipeline', label: 'PR Review Pipeline' },
] as const

interface Repository {
  name: string
}

interface CreateTaskFormState {
  title: string
  repository: string
  pipeline: string
  priority: number
  description: string
}

const EMPTY_FORM: CreateTaskFormState = {
  title: '',
  repository: '',
  pipeline: 'feature-pipeline',
  priority: 5,
  description: '',
}

interface CreateTaskDialogProps {
  open: boolean
  onClose: () => void
  onSuccess: () => void
  repositories: Repository[]
}

export function CreateTaskDialog({
  open,
  onClose,
  onSuccess,
  repositories,
}: CreateTaskDialogProps) {
  const [form, setForm] = useState<CreateTaskFormState>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)

  const [createTask, { loading }] = useMutation(CREATE_TASK, {
    onCompleted: (result) => {
      const errors = result?.createTask?.errors
      if (errors?.length) {
        setFormError(errors.map((e: { message: string }) => e.message).join(', '))
      } else {
        handleClose()
        onSuccess()
      }
    },
    onError: (err) => {
      setFormError(err.message)
    },
  })

  const handleClose = useCallback(() => {
    setForm(EMPTY_FORM)
    setFormError(null)
    onClose()
  }, [onClose])

  const handleSubmit = () => {
    if (!form.title.trim()) {
      setFormError('Title is required.')
      return
    }
    if (!form.repository) {
      setFormError('Repository is required.')
      return
    }

    setFormError(null)

    const initialContext = form.description.trim()
      ? { description: form.description.trim() }
      : undefined

    createTask({
      variables: {
        input: {
          title: form.title.trim(),
          repository: form.repository,
          source: 'web-ui',
          pipeline: form.pipeline,
          priority: form.priority,
          initialContext,
        },
      },
    })
  }

  const handlePriorityChange = (_: Event, value: number | number[]) => {
    setForm((f) => ({ ...f, priority: value as number }))
  }

  const priorityMarks = [
    { value: 0, label: '0' },
    { value: 25, label: '25' },
    { value: 50, label: '50' },
    { value: 75, label: '75' },
    { value: 100, label: '100' },
  ]

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create Task</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {formError && <Alert severity="error">{formError}</Alert>}

          <TextField
            label="Title"
            required
            fullWidth
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Describe the task for the agent"
            data-testid="task-form-title"
          />

          <FormControl fullWidth required>
            <InputLabel>Repository</InputLabel>
            <Select
              value={form.repository}
              label="Repository"
              onChange={(e) => setForm((f) => ({ ...f, repository: e.target.value }))}
              data-testid="task-form-repository"
            >
              {repositories.length === 0 ? (
                <MenuItem value="" disabled>
                  No repositories available
                </MenuItem>
              ) : (
                repositories.map((repo) => (
                  <MenuItem key={repo.name} value={repo.name}>
                    {repo.name}
                  </MenuItem>
                ))
              )}
            </Select>
          </FormControl>

          <FormControl fullWidth required>
            <InputLabel>Pipeline</InputLabel>
            <Select
              value={form.pipeline}
              label="Pipeline"
              onChange={(e) => setForm((f) => ({ ...f, pipeline: e.target.value }))}
              data-testid="task-form-pipeline"
            >
              {PIPELINES.map((p) => (
                <MenuItem key={p.value} value={p.value}>
                  {p.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <Box>
            <Typography gutterBottom>
              Priority: <strong>{form.priority}</strong>
            </Typography>
            <Slider
              value={form.priority}
              onChange={handlePriorityChange}
              min={0}
              max={100}
              step={1}
              marks={priorityMarks}
              valueLabelDisplay="auto"
              data-testid="task-form-priority"
            />
            <Typography variant="caption" color="text.secondary">
              Higher priority tasks are processed first (0-100, default 5)
            </Typography>
          </Box>

          <TextField
            label="Description"
            multiline
            minRows={3}
            maxRows={10}
            fullWidth
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Describe what the agent should do..."
            helperText="Provide enough detail for the agent to understand the task"
            data-testid="task-form-description"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} data-testid="task-form-cancel">
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={loading}
          data-testid="task-form-submit"
        >
          {loading ? 'Creating...' : 'Create Task'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default CreateTaskDialog
