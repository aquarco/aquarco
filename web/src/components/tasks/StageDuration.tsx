'use client'

import React, { useState, useEffect, useCallback } from 'react'
import Typography from '@mui/material/Typography'
import { formatDurationSeconds } from '@/app/tasks/[id]/utils'

interface StageDurationProps {
  startedAt: string | null
  completedAt: string | null
  isExecuting: boolean
}

export function StageDuration({ startedAt, completedAt, isExecuting }: StageDurationProps) {
  const computeSeconds = useCallback(() => {
    if (!startedAt) return 0
    const end = completedAt ? new Date(completedAt).getTime() : Date.now()
    return Math.max(0, Math.floor((end - new Date(startedAt).getTime()) / 1000))
  }, [startedAt, completedAt])

  const [seconds, setSeconds] = useState(computeSeconds)

  useEffect(() => {
    setSeconds(computeSeconds())
    if (!isExecuting) return
    const id = setInterval(() => setSeconds(computeSeconds()), 1000)
    return () => clearInterval(id)
  }, [isExecuting, computeSeconds])

  if (!startedAt) return null
  return (
    <Typography variant="caption" color="text.secondary">
      {formatDurationSeconds(seconds)}
    </Typography>
  )
}

export default StageDuration
