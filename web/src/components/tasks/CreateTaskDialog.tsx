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

const TASK_CATEGORIES = [
  { value: 'ANALYZE', label: 'Analyze' },
  { value: 'REVIEW', label: 'Review' },
  { value: 'IMPLEMENTATION', label: 'Implementation' },
  { value: 'TEST', label: 'Test' },
  { value: 'DESIGN', label: 'Design' },
  { value: 'DOCS', label: 'Docs' },
] as const

interface Repository {
  name: string
}

interface CreateTaskFormState {
  title: string
  category: string
  repository: string
  priority: number
  initialContext: string
}

const EMPTY_FORM: CreateTaskFormState = {
  title: '',
  category: 'ANALYZE',
  repository: '',
  priority: 5,
  initialContext: '',
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
  const [jsonError, setJsonError] = useState<string | null>(null)

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
    setJsonError(null)
    onClose()
  }, [onClose])

  const validateJson = (value: string): boolean => {
    if (!value.trim()) {
      setJsonError(null)
      return true
    }
    try {
      JSON.parse(value)
      setJsonError(null)
      return true
    } catch {
      setJsonError('Invalid JSON format')
      return false
    }
  }

  const handleSubmit = () => {
    // Validate required fields
    if (!form.title.trim()) {
      setFormError('Title is required.')
      return
    }
    if (!form.repository) {
      setFormError('Repository is required.')
      return
    }

    // Validate JSON if provided
    if (!validateJson(form.initialContext)) {
      return
    }

    setFormError(null)

    // Parse initial context if provided
    let initialContext = undefined
    if (form.initialContext.trim()) {
      try {
        initialContext = JSON.parse(form.initialContext)
      } catch {
        setFormError('Invalid JSON in initial context.')
        return
      }
    }

    createTask({
      variables: {
        input: {
          title: form.title.trim(),
          category: form.category,
          repository: form.repository,
          source: 'web-ui',
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
            <InputLabel>Category</InputLabel>
            <Select
              value={form.category}
              label="Category"
              onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
              data-testid="task-form-category"
            >
              {TASK_CATEGORIES.map((cat) => (
                <MenuItem key={cat.value} value={cat.value}>
                  {cat.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

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
            label="Initial Context (optional)"
            multiline
            minRows={4}
            maxRows={10}
            fullWidth
            value={form.initialContext}
            onChange={(e) => {
              setForm((f) => ({ ...f, initialContext: e.target.value }))
              if (e.target.value.trim()) {
                validateJson(e.target.value)
              } else {
                setJsonError(null)
              }
            }}
            placeholder='{"key": "value"}'
            error={!!jsonError}
            helperText={jsonError || 'JSON object with additional context for the agent'}
            InputProps={{
              sx: { fontFamily: 'monospace', fontSize: '0.875rem' },
            }}
            data-testid="task-form-initial-context"
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
          disabled={loading || !!jsonError}
          data-testid="task-form-submit"
        >
          {loading ? 'Creating...' : 'Create Task'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default CreateTaskDialog
