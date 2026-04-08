'use client'

/**
 * Task action buttons and unblock dialog for the task detail page.
 *
 * Provides Retry, Rerun, Close, Cancel, and Unblock actions based on
 * the current task status.
 */

import React, { useState } from 'react'
import { useMutation } from '@apollo/client'
import Stack from '@mui/material/Stack'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Alert from '@mui/material/Alert'
import { RETRY_TASK, RERUN_TASK, CLOSE_TASK, CANCEL_TASK, UNBLOCK_TASK } from '@/lib/graphql/queries'

interface TaskActionsProps {
  taskId: string
  status: string
  onMutationComplete: () => void
}

export function TaskActions({ taskId, status, onMutationComplete }: TaskActionsProps) {
  const [unblockOpen, setUnblockOpen] = useState(false)
  const [resolution, setResolution] = useState('')
  const [mutationError, setMutationError] = useState<string | null>(null)

  const handleCompleted = (field: string) => (result: Record<string, unknown>) => {
    const payload = result?.[field] as { errors?: { message: string }[] } | undefined
    const errors = payload?.errors
    if (errors?.length) {
      setMutationError(errors.map((e) => e.message).join(', '))
    } else {
      setMutationError(null)
      onMutationComplete()
    }
  }

  const [retryTask, { loading: retrying }] = useMutation(RETRY_TASK, {
    variables: { id: taskId },
    onCompleted: handleCompleted('retryTask'),
  })

  const [rerunTask, { loading: rerunning }] = useMutation(RERUN_TASK, {
    variables: { id: taskId },
    onCompleted: handleCompleted('rerunTask'),
  })

  const [closeTask, { loading: closing }] = useMutation(CLOSE_TASK, {
    variables: { id: taskId },
    onCompleted: handleCompleted('closeTask'),
  })

  const [cancelTask, { loading: cancelling }] = useMutation(CANCEL_TASK, {
    variables: { id: taskId },
    onCompleted: handleCompleted('cancelTask'),
  })

  const [unblockTask, { loading: unblocking }] = useMutation(UNBLOCK_TASK, {
    onCompleted: (result) => {
      const errors = (result?.unblockTask as { errors?: { message: string }[] } | undefined)?.errors
      if (errors?.length) {
        setMutationError(errors.map((e) => e.message).join(', '))
      } else {
        setMutationError(null)
        setUnblockOpen(false)
        setResolution('')
        onMutationComplete()
      }
    },
  })

  const upper = status?.toUpperCase()
  const canRetry = upper === 'FAILED' || upper === 'RATE_LIMITED' || upper === 'TIMEOUT'
  const canRerun = upper === 'COMPLETED' || upper === 'FAILED' || upper === 'CLOSED'
  const canClose = upper === 'COMPLETED'
  const canCancel = upper === 'PENDING' || upper === 'QUEUED' || upper === 'EXECUTING'
  const canUnblock = upper === 'BLOCKED'

  return (
    <>
      {mutationError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setMutationError(null)}>
          {mutationError}
        </Alert>
      )}

      <Stack direction="row" spacing={1}>
        {canRetry && (
          <Button variant="contained" color="warning" onClick={() => retryTask()} disabled={retrying} data-testid="btn-retry">
            {retrying ? 'Retrying\u2026' : 'Retry'}
          </Button>
        )}
        {canRerun && (
          <Button variant="contained" color="info" onClick={() => rerunTask()} disabled={rerunning} data-testid="btn-rerun">
            {rerunning ? 'Creating\u2026' : 'Rerun'}
          </Button>
        )}
        {canClose && (
          <Button variant="outlined" color="secondary" onClick={() => closeTask()} disabled={closing} data-testid="btn-close">
            {closing ? 'Closing\u2026' : 'Close'}
          </Button>
        )}
        {canCancel && (
          <Button variant="outlined" color="error" onClick={() => cancelTask()} disabled={cancelling} data-testid="btn-cancel">
            {cancelling ? 'Cancelling\u2026' : 'Cancel'}
          </Button>
        )}
        {canUnblock && (
          <Button variant="contained" color="primary" onClick={() => setUnblockOpen(true)} data-testid="btn-unblock">
            Unblock
          </Button>
        )}
      </Stack>

      <Dialog open={unblockOpen} onClose={() => setUnblockOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Unblock Task</DialogTitle>
        <DialogContent>
          <TextField
            label="Resolution" multiline rows={4} fullWidth
            value={resolution} onChange={(e) => setResolution(e.target.value)}
            placeholder="Describe how this blockage was resolved..."
            sx={{ mt: 1 }} data-testid="unblock-resolution-input"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUnblockOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => unblockTask({ variables: { id: taskId, resolution } })}
            disabled={unblocking || !resolution.trim()}
            data-testid="btn-unblock-confirm"
          >
            {unblocking ? 'Unblocking\u2026' : 'Unblock'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
