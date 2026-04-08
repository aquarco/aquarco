/**
 * Context inspector accordion for the task detail page.
 *
 * Displays each context entry (JSON, text, or file ref) in an expandable row.
 */

import React from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Stack from '@mui/material/Stack'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { monoStyle } from '@/lib/theme'
import { formatDate } from '@/lib/format'
import type { ContextEntry } from '@/app/tasks/[id]/types'

function resolveContextValue(entry: ContextEntry): { display: string; isJson: boolean } {
  if (entry.valueJson != null) {
    return { display: JSON.stringify(entry.valueJson, null, 2), isJson: true }
  }
  if (entry.valueText != null) {
    return { display: entry.valueText, isJson: false }
  }
  if (entry.valueFileRef != null) {
    return { display: entry.valueFileRef, isJson: false }
  }
  return { display: '\u2014', isJson: false }
}

interface ContextInspectorProps {
  context: ContextEntry[]
}

export function ContextInspector({ context }: ContextInspectorProps) {
  if (!context || context.length === 0) return null

  return (
    <Box sx={{ mb: 2 }}>
      <Typography variant="subtitle1" fontWeight={700} gutterBottom>
        Context
      </Typography>
      {context.map((entry) => {
        const { display, isJson } = resolveContextValue(entry)
        return (
          <Accordion key={entry.id} variant="outlined" disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack direction="row" spacing={2} alignItems="center">
                <Typography sx={monoStyle} component="span">{entry.key}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {isJson ? 'JSON' : entry.valueType}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {formatDate(entry.createdAt)}
                </Typography>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Box
                component="pre"
                sx={{
                  m: 0, p: 1.5,
                  backgroundColor: 'background.default',
                  borderRadius: 1, overflow: 'auto',
                  ...monoStyle, fontSize: '0.78rem',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}
              >
                {display}
              </Box>
            </AccordionDetails>
          </Accordion>
        )
      })}
    </Box>
  )
}
