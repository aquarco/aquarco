'use client'

import Chip, { type ChipProps } from '@mui/material/Chip'

type StatusValue =
  | 'PENDING'
  | 'QUEUED'
  | 'PLANNING'
  | 'EXECUTING'
  | 'COMPLETED'
  | 'FAILED'
  | 'TIMEOUT'
  | 'BLOCKED'
  | 'SKIPPED'
  | string

interface StatusChipProps {
  status: StatusValue
  size?: ChipProps['size']
}

function getColorForStatus(status: StatusValue): ChipProps['color'] {
  switch (status?.toUpperCase()) {
    case 'PENDING':
    case 'QUEUED':
      return 'default'
    case 'PLANNING':
    case 'EXECUTING':
      return 'warning'
    case 'COMPLETED':
      return 'success'
    case 'FAILED':
    case 'TIMEOUT':
    case 'CANCELLED':
      return 'error'
    case 'BLOCKED':
      return 'warning'
    case 'SKIPPED':
      return 'default'
    default:
      return 'default'
  }
}

export function StatusChip({ status, size = 'small' }: StatusChipProps) {
  return (
    <Chip
      label={status}
      color={getColorForStatus(status)}
      size={size}
      data-testid={`status-chip-${status}`}
    />
  )
}

export default StatusChip
