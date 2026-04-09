'use client'

import React from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import { monoStyle } from '@/lib/theme'
import { toSectionTitle, isFindingArray } from '@/app/tasks/[id]/utils'
import type { FindingItem } from '@/app/tasks/[id]/utils'

interface StructuredOutputDisplayProps {
  output: Record<string, unknown>
}

export function StructuredOutputDisplay({ output }: StructuredOutputDisplayProps) {
  const entries = Object.entries(output).filter(([key]) => !key.startsWith('_'))
  if (entries.length === 0) return null

  return (
    <Stack spacing={2}>
      {entries.map(([key, value]) => {
        const title = toSectionTitle(key)

        // String value → heading + body
        if (typeof value === 'string') {
          return (
            <Box key={key}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                {title}
              </Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {value}
              </Typography>
            </Box>
          )
        }

        // Array of findings (objects with severity/message)
        if (Array.isArray(value) && isFindingArray(value)) {
          return (
            <Box key={key}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                {title}
              </Typography>
              <Stack spacing={1}>
                {value.map((f: FindingItem, i: number) => (
                  <Box
                    key={i}
                    sx={{
                      p: 1.5,
                      borderRadius: 1,
                      backgroundColor: 'background.default',
                      border: '1px solid',
                      borderColor: 'divider',
                    }}
                  >
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" fontWeight={600}>
                        {i + 1}.
                      </Typography>
                      {f.severity && (
                        <Chip
                          label={f.severity}
                          size="small"
                          color={
                            f.severity === 'error' || f.severity === 'critical'
                              ? 'error'
                              : f.severity === 'warning'
                                ? 'warning'
                                : 'default'
                          }
                        />
                      )}
                      {f.file && (
                        <Typography variant="caption" sx={monoStyle}>
                          {f.file}{f.line != null ? `:${f.line}` : ''}
                        </Typography>
                      )}
                    </Stack>
                    {f.message && (
                      <Typography variant="body2">{f.message}</Typography>
                    )}
                  </Box>
                ))}
              </Stack>
            </Box>
          )
        }

        // Plain array (strings, numbers, etc.)
        if (Array.isArray(value)) {
          return (
            <Box key={key}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                {title}
              </Typography>
              <Box component="ol" sx={{ m: 0, pl: 3 }}>
                {value.map((item, i) => (
                  <Typography component="li" variant="body2" key={i} sx={{ mb: 0.5 }}>
                    {typeof item === 'object' ? JSON.stringify(item) : String(item)}
                  </Typography>
                ))}
              </Box>
            </Box>
          )
        }

        // Number / boolean
        if (typeof value === 'number' || typeof value === 'boolean') {
          return (
            <Box key={key}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                {title}
              </Typography>
              <Typography variant="body2">{String(value)}</Typography>
            </Box>
          )
        }

        // Object fallback → JSON pre
        if (typeof value === 'object' && value != null) {
          return (
            <Box key={key}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>
                {title}
              </Typography>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1.5,
                  backgroundColor: 'background.default',
                  borderRadius: 1,
                  overflow: 'auto',
                  ...monoStyle,
                  fontSize: '0.78rem',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {JSON.stringify(value, null, 2)}
              </Box>
            </Box>
          )
        }

        return null
      })}
    </Stack>
  )
}

export default StructuredOutputDisplay
