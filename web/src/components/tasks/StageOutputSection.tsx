/**
 * Stage output accordion list for the task detail page.
 *
 * Renders each stage run as an expandable accordion with status, cost,
 * token stats, structured output, and live output stream.
 */

import React from 'react'
import { useQuery } from '@apollo/client'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Stack from '@mui/material/Stack'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { StatusChip } from '@/components/ui/StatusChip'
import { monoStyle } from '@/lib/theme'
import { formatCost, formatTokens } from '@/lib/spending'
import { StructuredOutputDisplay } from '@/components/tasks/StructuredOutputDisplay'
import { StageDuration } from '@/components/tasks/StageDuration'
import { parseLiveOutput } from '@/app/tasks/[id]/utils'
import { GET_PIPELINE_DEFINITIONS } from '@/lib/graphql/queries'
import type { Stage, PipelineStageDefn } from '@/app/tasks/[id]/types'

interface StageOutputSectionProps {
  stages: Stage[]
  effectiveExecutingStages: Set<number>
  pipelineName: string
}

export function StageOutputSection({ stages, effectiveExecutingStages, pipelineName }: StageOutputSectionProps) {
  const { data: pipeData } = useQuery(GET_PIPELINE_DEFINITIONS)
  const pipelineDefs = (pipeData?.pipelineDefinitions ?? []) as Array<{
    name: string
    stages: PipelineStageDefn[]
  }>
  const defn = pipelineDefs.find((p) => p.name === pipelineName)
  const defnStages = defn?.stages ?? []

  if (stages.length === 0) return null

  const runCountPerStageNumber = new Map<number, number>()
  const items: React.ReactNode[] = []

  for (const stage of stages) {
    const stageNum = stage.stageNumber
    const runCount = (runCountPerStageNumber.get(stageNum) ?? 0) + 1
    runCountPerStageNumber.set(stageNum, runCount)

    let runSuffix = ''
    if (runCount === 2) runSuffix = ' (2nd run)'
    else if (runCount === 3) runSuffix = ' (3rd run)'
    else if (runCount > 3) runSuffix = ` (${runCount}th run)`

    const stageName = defnStages[stageNum]?.name ?? stage.category.toUpperCase()

    const effectiveStatus = stage.status === 'PENDING' && effectiveExecutingStages.has(stage.stageNumber)
      ? 'EXECUTING' : stage.status
    const isLive = effectiveStatus === 'EXECUTING'
    const stageCost = stage.costUsd
    const stageTotalTokens = (stage.tokensInput ?? 0) + (stage.tokensOutput ?? 0) + (stage.cacheReadTokens ?? 0) + (stage.cacheWriteTokens ?? 0)

    const output = stage.structuredOutput as Record<string, unknown> | null
    const conditionMessage = output?._condition_message as string | undefined

    items.push(
      <Accordion key={stage.id} variant="outlined" disableGutters sx={{ '&:before': { display: 'none' } }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2, minHeight: 48 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ width: '100%', mr: 1 }}>
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Box sx={{ minWidth: 120, display: 'flex', justifyContent: 'flex-end' }}>
                <StatusChip status={effectiveStatus} size="small" />
              </Box>
              <Typography variant="body2" fontWeight={600}>
                {stageName}{runSuffix}
              </Typography>
              {stage.agent && (
                <Typography variant="caption" color="text.secondary">
                  {stage.agent}
                </Typography>
              )}
            </Stack>
            <Stack direction="row" spacing={1.5} alignItems="center">
              {stageCost != null && stageCost > 0 && (
                <Typography variant="caption" fontWeight={600} color="warning.main">
                  {isLive ? '~' : ''}{formatCost(stageCost)}
                </Typography>
              )}
              {stageTotalTokens > 0 && (
                <Typography variant="caption" color="text.secondary" sx={monoStyle}>
                  {formatTokens(stageTotalTokens)}
                </Typography>
              )}
              <StageDuration
                startedAt={stage.startedAt}
                completedAt={stage.completedAt}
                isExecuting={isLive}
              />
            </Stack>
          </Stack>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 2, pt: 0, pb: 2 }}>
          <TokenStatsBar stage={stage} />
          {output && <StructuredOutputDisplay output={output} />}
          <LiveOutputStream liveOutput={stage.liveOutput} />
        </AccordionDetails>
      </Accordion>
    )

    if (conditionMessage && stage.status === 'COMPLETED') {
      items.push(
        <Box
          key={`eval-${stage.id}`}
          sx={{
            px: 2, py: 1,
            borderLeft: '4px solid', borderColor: 'info.main',
            backgroundColor: 'action.hover', borderRadius: '0 4px 4px 0',
          }}
        >
          <Stack direction="row" spacing={1} alignItems="baseline">
            <Typography variant="caption" fontWeight={700} color="info.main" sx={{ textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Evaluation
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {conditionMessage}
            </Typography>
          </Stack>
        </Box>
      )
    }
  }

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Stage Output
        </Typography>
        <Stack spacing={1}>{items}</Stack>
      </CardContent>
    </Card>
  )
}

/** Token breakdown bar shown inside each stage accordion. */
function TokenStatsBar({ stage }: { stage: Stage }) {
  const inp = stage.tokensInput
  const out = stage.tokensOutput
  const cr = stage.cacheReadTokens
  const cw = stage.cacheWriteTokens
  if (!inp && !out) return null
  return (
    <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap justifyContent="flex-end"
      sx={{ mb: 2, py: 1, px: 1.5, borderRadius: 1, backgroundColor: 'action.hover' }}
    >
      {inp != null && inp > 0 && (
        <Typography variant="caption" color="text.secondary">Input: {formatTokens(inp)}</Typography>
      )}
      {out != null && out > 0 && (
        <Typography variant="caption" color="text.secondary">Output: {formatTokens(out)}</Typography>
      )}
      {cr != null && cr > 0 && (
        <Typography variant="caption" color="text.secondary">Cache Read: {formatTokens(cr)}</Typography>
      )}
      {cw != null && cw > 0 && (
        <Typography variant="caption" color="text.secondary">Cache Write: {formatTokens(cw)}</Typography>
      )}
    </Stack>
  )
}

/** Parsed live output stream display. */
function LiveOutputStream({ liveOutput }: { liveOutput: string | null }) {
  if (!liveOutput) return null
  const lines = parseLiveOutput(liveOutput)
  if (lines.length === 0) return null
  return (
    <Box
      sx={{
        mt: 1, p: 1.5,
        backgroundColor: '#1e1e1e', color: '#d4d4d4',
        borderRadius: 1, overflow: 'auto', maxHeight: 400,
        ...monoStyle, fontSize: '0.75rem',
      }}
    >
      {lines.map((line, i) => (
        <Typography
          key={i}
          variant="body2"
          component="div"
          sx={{
            color: '#d4d4d4', ...monoStyle,
            fontSize: '0.75rem', whiteSpace: 'pre-wrap',
            wordBreak: 'break-word', mb: 0.5,
          }}
        >
          {line}
        </Typography>
      ))}
    </Box>
  )
}
