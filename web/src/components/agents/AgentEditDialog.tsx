'use client'

import React, { useState, useEffect } from 'react'
import { useMutation } from '@apollo/client'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import TextField from '@mui/material/TextField'
import Stack from '@mui/material/Stack'
import Alert from '@mui/material/Alert'
import Typography from '@mui/material/Typography'
import CircularProgress from '@mui/material/CircularProgress'
import Switch from '@mui/material/Switch'
import FormControlLabel from '@mui/material/FormControlLabel'
import { MODIFY_AGENT } from '@/lib/graphql/queries'
import type { AgentDefinitionRow } from './AgentTable'

interface AgentEditDialogProps {
  agent: AgentDefinitionRow | null
  scope: string
  open: boolean
  onClose: () => void
  onSaved: () => void
}

export default function AgentEditDialog({
  agent,
  scope,
  open,
  onClose,
  onSaved,
}: AgentEditDialogProps) {
  const [jsonMode, setJsonMode] = useState(false)
  const [specText, setSpecText] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [modifyAgent, { loading }] = useMutation(MODIFY_AGENT)

  useEffect(() => {
    if (agent) {
      setSpecText(JSON.stringify(agent.spec, null, 2))
      setError(null)
      setJsonMode(false)
    }
  }, [agent])

  if (!agent) return null

  const spec = agent.spec as Record<string, unknown> | null

  async function handleSave() {
    setError(null)
    try {
      const parsedSpec = JSON.parse(specText)
      const result = await modifyAgent({
        variables: {
          name: agent!.name,
          scope,
          spec: parsedSpec,
        },
      })
      const errors = result.data?.modifyAgent?.errors
      if (errors && errors.length > 0) {
        setError(errors.map((e: { message: string }) => e.message).join(', '))
        return
      }
      onSaved()
      onClose()
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError('Invalid JSON. Please check the spec format.')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to save')
      }
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        Edit Agent: {agent.name}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          <Typography variant="body2" color="text.secondary">
            Modify the agent spec below. Changes will be saved as an override and
            can be submitted as a PR to the config repository.
          </Typography>

          <FormControlLabel
            control={
              <Switch
                checked={jsonMode}
                onChange={(e) => setJsonMode(e.target.checked)}
                size="small"
              />
            }
            label="Raw JSON editor"
          />

          {jsonMode ? (
            <TextField
              label="Agent Spec (JSON)"
              multiline
              minRows={12}
              maxRows={24}
              value={specText}
              onChange={(e) => setSpecText(e.target.value)}
              fullWidth
              inputProps={{
                style: { fontFamily: 'monospace', fontSize: '0.85rem' },
                'data-testid': 'agent-spec-editor',
              }}
            />
          ) : (
            <Stack spacing={2}>
              <TextField
                label="Timeout (minutes)"
                type="number"
                size="small"
                defaultValue={(spec as Record<string, unknown>)?.resources
                  ? ((spec as Record<string, unknown>).resources as Record<string, unknown>)?.timeoutMinutes ?? ''
                  : ''}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(specText)
                    if (!parsed.resources) parsed.resources = {}
                    parsed.resources.timeoutMinutes = e.target.value ? Number(e.target.value) : undefined
                    setSpecText(JSON.stringify(parsed, null, 2))
                  } catch { /* ignore parse errors */ }
                }}
              />
              <TextField
                label="Max Turns"
                type="number"
                size="small"
                defaultValue={(spec as Record<string, unknown>)?.resources
                  ? ((spec as Record<string, unknown>).resources as Record<string, unknown>)?.maxTurns ?? ''
                  : ''}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(specText)
                    if (!parsed.resources) parsed.resources = {}
                    parsed.resources.maxTurns = e.target.value ? Number(e.target.value) : undefined
                    setSpecText(JSON.stringify(parsed, null, 2))
                  } catch { /* ignore parse errors */ }
                }}
              />
              <TextField
                label="Max Cost (USD)"
                type="number"
                size="small"
                defaultValue={(spec as Record<string, unknown>)?.resources
                  ? ((spec as Record<string, unknown>).resources as Record<string, unknown>)?.maxCost ?? ''
                  : ''}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(specText)
                    if (!parsed.resources) parsed.resources = {}
                    parsed.resources.maxCost = e.target.value ? Number(e.target.value) : undefined
                    setSpecText(JSON.stringify(parsed, null, 2))
                  } catch { /* ignore parse errors */ }
                }}
              />
              <TextField
                label="Max Concurrent"
                type="number"
                size="small"
                defaultValue={(spec as Record<string, unknown>)?.resources
                  ? ((spec as Record<string, unknown>).resources as Record<string, unknown>)?.maxConcurrent ?? ''
                  : ''}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(specText)
                    if (!parsed.resources) parsed.resources = {}
                    parsed.resources.maxConcurrent = e.target.value ? Number(e.target.value) : undefined
                    setSpecText(JSON.stringify(parsed, null, 2))
                  } catch { /* ignore parse errors */ }
                }}
              />
              <TextField
                label="Full Spec (JSON)"
                multiline
                minRows={6}
                maxRows={12}
                value={specText}
                onChange={(e) => setSpecText(e.target.value)}
                fullWidth
                inputProps={{
                  style: { fontFamily: 'monospace', fontSize: '0.8rem' },
                }}
                helperText="Editing fields above will update this JSON automatically"
              />
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={loading}
          startIcon={loading ? <CircularProgress size={18} /> : undefined}
          data-testid="agent-save-btn"
        >
          Save Override
        </Button>
      </DialogActions>
    </Dialog>
  )
}
