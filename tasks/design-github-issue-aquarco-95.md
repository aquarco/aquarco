# Design: Stage Output Detail (Issue #95)

## Overview

Improve the Stage Output section in the task detail page (`web/src/app/tasks/[id]/page.tsx`):

1. **Remove `raw_output`** — stop fetching and rendering `rawOutput` entirely.
2. **Parse `live_output`** — parse each newline-delimited JSON line and extract only meaningful signal fields.
3. **Convert `structured_output` to Markdown** — render every field in `structuredOutput` as a human-readable `## Section` with smart list formatting.

---

## Files to Modify

| File | Change |
|------|--------|
| `web/src/lib/graphql/queries.ts` | Remove `rawOutput` from the `GET_TASK` query's `stages` selection set |
| `web/src/app/tasks/[id]/page.tsx` | (a) Remove `rawOutput` from `Stage` interface, (b) add `parseLiveOutput` helper, (c) add `StructuredOutputDisplay` component, (d) wire both into the accordion |

---

## Detailed Design

### 1. Remove `rawOutput` from GraphQL query

In `queries.ts`, inside `GET_TASK` → `stages { … }`, delete line:
```
rawOutput
```

### 2. Remove `rawOutput` from `Stage` interface

In `page.tsx`, remove the `rawOutput: string | null` property from the `Stage` interface (line 46).

### 3. `parseLiveOutput` helper function

```ts
/**
 * Parses newline-delimited JSON from live_output and extracts human-readable
 * signal lines. Silently skips lines that are not valid JSON or contain no
 * recognised fields.
 */
function parseLiveOutput(raw: string): string[] {
  const results: string[] = []

  for (const line of raw.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue

    let obj: unknown
    try {
      obj = JSON.parse(trimmed)
    } catch {
      continue  // skip non-JSON lines silently
    }

    if (typeof obj !== 'object' || obj === null) continue
    const o = obj as Record<string, unknown>

    // Direct top-level fields
    const directText =
      (typeof o.stdout === 'string' ? o.stdout : null) ??
      (typeof o.output === 'string' ? o.output : null)
    if (directText) { results.push(directText); continue }

    // tool_use_result variants
    if (o.tool_use_result !== undefined) {
      const tur = o.tool_use_result
      if (typeof tur === 'string') { results.push(tur); continue }
      if (typeof tur === 'object' && tur !== null) {
        const t = tur as Record<string, unknown>
        const turText =
          (typeof t.stdout === 'string' ? t.stdout : null) ??
          (typeof t.stderr === 'string' ? t.stderr : null) ??
          (typeof t.content === 'string' ? t.content : null) ??
          ((t.file && typeof (t.file as Record<string, unknown>).filePath === 'string')
            ? `File: ${(t.file as Record<string, unknown>).filePath}` : null)
        if (turText) { results.push(turText); continue }
      }
    }

    // message.content array
    const content = (o.message as Record<string, unknown> | undefined)?.content
    if (Array.isArray(content)) {
      for (const block of content) {
        if (typeof block !== 'object' || block === null) continue
        const b = block as Record<string, unknown>
        const blockText =
          (typeof b.thinking === 'string' ? b.thinking : null) ??
          (typeof b.text === 'string' ? b.text : null) ??
          (typeof b.content === 'string' ? b.content : null) ??
          ((b.input && typeof (b.input as Record<string, unknown>).description === 'string')
            ? (b.input as Record<string, unknown>).description as string : null) ??
          ((b.input && typeof (b.input as Record<string, unknown>).file_path === 'string')
            ? `File: ${(b.input as Record<string, unknown>).file_path}` : null)
        if (blockText) results.push(blockText as string)
      }
      if (results.length > 0) continue
    }
  }

  return results
}
```

**Rendering** — replace the current raw `<pre>{stage.liveOutput}</pre>` block with:

```tsx
{effectiveStatus === 'EXECUTING' && stage.liveOutput && (() => {
  const lines = parseLiveOutput(stage.liveOutput)
  if (lines.length === 0) return null
  return (
    <Box sx={{ mt: 1, p: 1.5, backgroundColor: '#1e1e1e', borderRadius: 1, maxHeight: 400, overflow: 'auto' }}>
      {lines.map((line, i) => (
        <Typography key={i} variant="caption" component="div"
          sx={{ color: '#d4d4d4', fontFamily: 'monospace', fontSize: '0.75rem',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', mb: 0.5 }}>
          {line}
        </Typography>
      ))}
    </Box>
  )
})()}
```

### 4. `StructuredOutputDisplay` component

This replaces both the existing findings/summary/recommendation hard-coded rendering **and** the raw JSON pre-block fallback. The component renders every non-underscore-prefixed field in `structuredOutput`.

```tsx
/** Converts a snake_case or camelCase key to a Title Case section heading */
function toSectionTitle(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')      // camelCase → spaced
    .replace(/_/g, ' ')               // snake_case → spaced
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim()
}

interface FindingItem {
  severity?: string
  file?: string
  line?: number
  message?: string
  [key: string]: unknown
}

function isFindingArray(arr: unknown[]): arr is FindingItem[] {
  return arr.length > 0 && typeof arr[0] === 'object' && arr[0] !== null &&
    ('message' in (arr[0] as object) || 'severity' in (arr[0] as object))
}

function StructuredOutputDisplay({ output }: { output: Record<string, unknown> }) {
  const theme = useTheme()
  const sections: React.ReactNode[] = []

  for (const [key, value] of Object.entries(output)) {
    if (key.startsWith('_')) continue  // skip internal metadata fields
    if (value === null || value === undefined) continue

    const title = toSectionTitle(key)

    if (typeof value === 'string') {
      sections.push(
        <Box key={key} sx={{ mb: 2 }}>
          <Typography variant="subtitle2" fontWeight={700} gutterBottom>{title}</Typography>
          <Typography variant="body2">{value}</Typography>
        </Box>
      )
    } else if (Array.isArray(value) && value.length > 0) {
      if (isFindingArray(value)) {
        // Findings-style: numbered list with severity chip + message + file ref
        sections.push(
          <Box key={key} sx={{ mb: 2 }}>
            <Typography variant="subtitle2" fontWeight={700} gutterBottom>{title}</Typography>
            <Stack spacing={1}>
              {(value as FindingItem[]).map((item, i) => (
                <Box key={i} sx={{
                  p: 1.5, borderRadius: 1,
                  backgroundColor: 'background.default',
                  border: '1px solid', borderColor: 'divider',
                }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                    <Typography variant="caption" color="text.secondary" fontWeight={700}>{i + 1}.</Typography>
                    {item.severity && (
                      <Chip label={item.severity} size="small"
                        color={
                          item.severity === 'error' || item.severity === 'critical' ? 'error' :
                          item.severity === 'warning' ? 'warning' : 'default'
                        }
                      />
                    )}
                    {item.file && (
                      <Typography variant="caption" sx={monoStyle}>
                        {item.file}{item.line ? `:${item.line}` : ''}
                      </Typography>
                    )}
                  </Stack>
                  {item.message && <Typography variant="body2">{item.message}</Typography>}
                </Box>
              ))}
            </Stack>
          </Box>
        )
      } else {
        // Generic array: numbered list
        sections.push(
          <Box key={key} sx={{ mb: 2 }}>
            <Typography variant="subtitle2" fontWeight={700} gutterBottom>{title}</Typography>
            <Stack component="ol" spacing={0.5} sx={{ pl: 2, m: 0 }}>
              {value.map((item, i) => (
                <Typography key={i} component="li" variant="body2">
                  {typeof item === 'string' ? item : JSON.stringify(item)}
                </Typography>
              ))}
            </Stack>
          </Box>
        )
      }
    } else if (typeof value === 'object') {
      // Fallback: render as JSON pre block
      sections.push(
        <Box key={key} sx={{ mb: 2 }}>
          <Typography variant="subtitle2" fontWeight={700} gutterBottom>{title}</Typography>
          <Box component="pre" sx={{
            m: 0, p: 1.5, backgroundColor: 'background.default',
            borderRadius: 1, overflow: 'auto', ...monoStyle,
            fontSize: '0.78rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>
            {JSON.stringify(value, null, 2)}
          </Box>
        </Box>
      )
    } else {
      // Primitive (number, boolean)
      sections.push(
        <Box key={key} sx={{ mb: 2 }}>
          <Typography variant="subtitle2" fontWeight={700} gutterBottom>{title}</Typography>
          <Typography variant="body2">{String(value)}</Typography>
        </Box>
      )
    }
  }

  if (sections.length === 0) return null
  return <>{sections}</>
}
```

**Wire it into the accordion** — replace the current three blocks (summary, recommendation, findings, and structured-output-fallback JSON block) with a single call:

```tsx
{output && (
  <StructuredOutputDisplay output={output as Record<string, unknown>} />
)}
```

Also remove the `findings`, `summary`, `recommendation`, `conditionMessage` destructuring from `output` since `StructuredOutputDisplay` handles all of them. Retain the evaluation block below the accordion (it reads `_condition_message` and displays separately) — this still works because we simply pass `output` directly to `StructuredOutputDisplay` which skips `_` prefixed keys, while the evaluation block reads from `output?._condition_message` independently.

### 5. Remove raw output fallback block

Delete lines 892–909 (`{/* Raw output fallback */}` block) from `page.tsx`. There is no replacement — `rawOutput` is no longer fetched.

---

## Assumptions

- `rawOutput` is not referenced by any other component or query in the web app (confirmed: only one query fetches it — `GET_TASK`).
- The `_` prefix convention for internal metadata fields (`_subtype`, `_is_error`, `_num_turns`, etc.) is stable. These will be suppressed in the Markdown renderer.
- Providing an empty live_output extraction (zero lines extracted) gracefully renders nothing — no empty box is shown.
- `_condition_message` is intentionally kept separate from `StructuredOutputDisplay` (it renders as the Evaluation banner, not a section heading).

---

## Non-Goals

- No markdown rendering library (e.g. react-markdown) is introduced — the field-to-section conversion uses MUI Typography components directly. This avoids a new dependency and keeps consistent styling.
- No GraphQL schema changes — `rawOutput` is dropped from the query only, not from the schema.
