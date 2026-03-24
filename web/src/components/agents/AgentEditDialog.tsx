'use client'

import React, { useState, useEffect } from 'react'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import TextField from '@mui/material/TextField'
import Alert from '@mui/material/Alert'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import type { AgentDefinition } from './AgentCard'

interface AgentEditDialogProps {
  open: boolean
  agent: AgentDefinition | null
  onClose: () => void
  onSave: (agentName: string, spec: unknown, scope: string, scopeRepository: string | null) => void
  saving?: boolean
  error?: string | null
}

export default function AgentEditDialog({
  open,
  agent,
  onClose,
  onSave,
  saving = false,
  error = null,
}: AgentEditDialogProps) {
  const [specText, setSpecText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)

  useEffect(() => {
    if (agent) {
      const spec = agent.modifiedSpec ?? agent.spec
      setSpecText(JSON.stringify(spec, null, 2))
      setParseError(null)
    }
  }, [agent])

  function handleSave() {
    if (!agent) return
    try {
      const parsed = JSON.parse(specText)
      setParseError(null)
      const scope = agent.source === 'REPOSITORY' ? 'repository' : 'global'
      onSave(agent.name, parsed, scope, agent.sourceRepository ?? null)
    } catch {
      setParseError('Invalid JSON')
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        Edit Agent: {agent?.name}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {agent?.source === 'DEFAULT' && (
            <Alert severity="info">
              Default agents cannot be modified directly. Changes are applied as overrides.
            </Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {parseError && <Alert severity="warning">{parseError}</Alert>}
          <Typography variant="caption" color="text.secondary">
            Agent spec (JSON). Changes will be saved as overrides in the database.
          </Typography>
          <TextField
            multiline
            minRows={15}
            maxRows={30}
            value={specText}
            onChange={(e) => setSpecText(e.target.value)}
            fullWidth
            inputProps={{
              style: { fontFamily: 'monospace', fontSize: '0.8rem' },
            }}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || !specText.trim()}
        >
          {saving ? 'Saving...' : 'Save Override'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
