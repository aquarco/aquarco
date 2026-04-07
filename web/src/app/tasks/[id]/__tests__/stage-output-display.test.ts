/**
 * Tests for Stage Output display logic (GitHub issue #95).
 *
 * Validates:
 * 1. parseLiveOutput — JSON line parsing and field extraction
 * 2. toSectionTitle — snake_case / camelCase → Title Case
 * 3. isFindingArray — type guard for structured findings
 * 4. formatDurationSeconds — human-readable duration formatting
 */

import { describe, it, expect } from 'vitest'
import { parseLiveOutput, toSectionTitle, isFindingArray, formatDurationSeconds } from '../utils'

// ===========================================================================
// Tests
// ===========================================================================

describe('parseLiveOutput', () => {
  it('returns empty array for empty string', () => {
    expect(parseLiveOutput('')).toEqual([])
  })

  it('returns empty array for whitespace-only input', () => {
    expect(parseLiveOutput('   \n  \n\n')).toEqual([])
  })

  it('skips non-JSON lines', () => {
    const input = 'not json\n{ invalid json\nstill not json'
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('extracts top-level stdout', () => {
    const input = JSON.stringify({ stdout: 'hello world' })
    expect(parseLiveOutput(input)).toEqual(['hello world'])
  })

  it('extracts top-level output', () => {
    const input = JSON.stringify({ output: 'build succeeded' })
    expect(parseLiveOutput(input)).toEqual(['build succeeded'])
  })

  it('extracts both stdout and output from same line', () => {
    const input = JSON.stringify({ stdout: 'line1', output: 'line2' })
    expect(parseLiveOutput(input)).toEqual(['line1', 'line2'])
  })

  it('ignores empty stdout/output strings', () => {
    const input = JSON.stringify({ stdout: '', output: '' })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('ignores non-string stdout/output', () => {
    const input = JSON.stringify({ stdout: 42, output: true })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('extracts message.content.thinking', () => {
    const input = JSON.stringify({
      message: { content: [{ thinking: 'I should check the database' }] },
    })
    expect(parseLiveOutput(input)).toEqual(['I should check the database'])
  })

  it('extracts message.content.text', () => {
    const input = JSON.stringify({
      message: { content: [{ text: 'Here is the result' }] },
    })
    expect(parseLiveOutput(input)).toEqual(['Here is the result'])
  })

  it('extracts message.content.content', () => {
    const input = JSON.stringify({
      message: { content: [{ content: 'File contents here' }] },
    })
    expect(parseLiveOutput(input)).toEqual(['File contents here'])
  })

  it('extracts message.content.input.description', () => {
    const input = JSON.stringify({
      message: { content: [{ input: { description: 'Read the config file' } }] },
    })
    expect(parseLiveOutput(input)).toEqual(['Read the config file'])
  })

  it('extracts message.content.input.file_path', () => {
    const input = JSON.stringify({
      message: { content: [{ input: { file_path: '/src/index.ts' } }] },
    })
    expect(parseLiveOutput(input)).toEqual(['/src/index.ts'])
  })

  it('extracts multiple fields from message.content array', () => {
    const input = JSON.stringify({
      message: {
        content: [
          { thinking: 'step 1' },
          { text: 'result text' },
          { input: { description: 'do something', file_path: '/a/b.ts' } },
        ],
      },
    })
    expect(parseLiveOutput(input)).toEqual([
      'step 1',
      'result text',
      'do something',
      '/a/b.ts',
    ])
  })

  it('skips non-object items in message.content array', () => {
    const input = JSON.stringify({
      message: { content: ['a string', null, 42, { text: 'valid' }] },
    })
    expect(parseLiveOutput(input)).toEqual(['valid'])
  })

  it('handles message.content that is not an array', () => {
    const input = JSON.stringify({ message: { content: 'plain string' } })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('extracts tool_use_result when string', () => {
    const input = JSON.stringify({ tool_use_result: 'success' })
    expect(parseLiveOutput(input)).toEqual(['success'])
  })

  it('ignores empty tool_use_result string', () => {
    const input = JSON.stringify({ tool_use_result: '' })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('extracts tool_use_result.stdout', () => {
    const input = JSON.stringify({ tool_use_result: { stdout: 'npm test passed' } })
    expect(parseLiveOutput(input)).toEqual(['npm test passed'])
  })

  it('extracts tool_use_result.stderr', () => {
    const input = JSON.stringify({ tool_use_result: { stderr: 'warning: deprecated' } })
    expect(parseLiveOutput(input)).toEqual(['warning: deprecated'])
  })

  it('extracts tool_use_result.content', () => {
    const input = JSON.stringify({ tool_use_result: { content: 'file read ok' } })
    expect(parseLiveOutput(input)).toEqual(['file read ok'])
  })

  it('extracts tool_use_result.file.filePath', () => {
    const input = JSON.stringify({
      tool_use_result: { file: { filePath: '/home/user/code.ts' } },
    })
    expect(parseLiveOutput(input)).toEqual(['/home/user/code.ts'])
  })

  it('extracts multiple tool_use_result fields from one line', () => {
    const input = JSON.stringify({
      tool_use_result: {
        stdout: 'out',
        stderr: 'err',
        content: 'body',
        file: { filePath: '/x.ts' },
      },
    })
    expect(parseLiveOutput(input)).toEqual(['out', 'err', 'body', '/x.ts'])
  })

  it('handles multi-line input with mixed JSON and non-JSON', () => {
    const lines = [
      JSON.stringify({ stdout: 'first' }),
      'garbage line',
      JSON.stringify({ output: 'second' }),
      '',
      JSON.stringify({ tool_use_result: 'third' }),
    ].join('\n')
    expect(parseLiveOutput(lines)).toEqual(['first', 'second', 'third'])
  })

  it('handles a realistic mixed payload across lines', () => {
    const lines = [
      JSON.stringify({ stdout: 'Installing dependencies...' }),
      JSON.stringify({
        message: {
          content: [
            { thinking: 'Analyzing imports' },
            { input: { description: 'Read package.json', file_path: '/app/package.json' } },
          ],
        },
      }),
      JSON.stringify({ tool_use_result: { stdout: 'npm install ok', file: { filePath: '/app/node_modules' } } }),
    ].join('\n')

    expect(parseLiveOutput(lines)).toEqual([
      'Installing dependencies...',
      'Analyzing imports',
      'Read package.json',
      '/app/package.json',
      'npm install ok',
      '/app/node_modules',
    ])
  })

  it('ignores JSON lines with no recognized fields', () => {
    const input = JSON.stringify({ foo: 'bar', baz: 123 })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles lines with leading/trailing whitespace', () => {
    const input = `  ${JSON.stringify({ stdout: 'trimmed' })}  `
    expect(parseLiveOutput(input)).toEqual(['trimmed'])
  })
})

describe('toSectionTitle', () => {
  it('converts snake_case to Title Case', () => {
    expect(toSectionTitle('my_field_name')).toBe('My Field Name')
  })

  it('converts camelCase to Title Case', () => {
    expect(toSectionTitle('myFieldName')).toBe('My Field Name')
  })

  it('converts single word', () => {
    expect(toSectionTitle('summary')).toBe('Summary')
  })

  it('converts already Title Case', () => {
    expect(toSectionTitle('Summary')).toBe('Summary')
  })

  it('handles mixed snake_case and camelCase', () => {
    expect(toSectionTitle('my_fieldName')).toBe('My Field Name')
  })

  it('handles empty string', () => {
    expect(toSectionTitle('')).toBe('')
  })

  it('converts real examples from issue', () => {
    expect(toSectionTitle('findings')).toBe('Findings')
    expect(toSectionTitle('recommendation')).toBe('Recommendation')
    expect(toSectionTitle('structured_output')).toBe('Structured Output')
    expect(toSectionTitle('coveragePercent')).toBe('Coverage Percent')
  })

  it('handles consecutive uppercase in camelCase', () => {
    // The regex only splits on lowercase→uppercase boundaries,
    // so consecutive uppercase letters stay grouped
    expect(toSectionTitle('parseHTMLOutput')).toBe('Parse HTMLOutput')
  })
})

describe('isFindingArray', () => {
  it('returns false for empty array', () => {
    expect(isFindingArray([])).toBe(false)
  })

  it('returns true for array of findings with severity and message', () => {
    const findings = [
      { severity: 'error', message: 'Something is wrong', file: 'a.ts', line: 10 },
      { severity: 'warning', message: 'Consider this', file: 'b.ts', line: 20 },
    ]
    expect(isFindingArray(findings)).toBe(true)
  })

  it('returns true for minimal findings (only severity and message)', () => {
    const findings = [{ severity: 'info', message: 'Note' }]
    expect(isFindingArray(findings)).toBe(true)
  })

  it('returns false if an item lacks severity', () => {
    const arr = [
      { severity: 'error', message: 'ok' },
      { message: 'missing severity' },
    ]
    expect(isFindingArray(arr)).toBe(false)
  })

  it('returns false if an item lacks message', () => {
    const arr = [
      { severity: 'error', message: 'ok' },
      { severity: 'warning' },
    ]
    expect(isFindingArray(arr)).toBe(false)
  })

  it('returns false for array of strings', () => {
    expect(isFindingArray(['a', 'b', 'c'] as unknown[])).toBe(false)
  })

  it('returns false for array of numbers', () => {
    expect(isFindingArray([1, 2, 3] as unknown[])).toBe(false)
  })

  it('returns false for array with null items', () => {
    expect(isFindingArray([null, null] as unknown[])).toBe(false)
  })

  it('returns false for mixed valid/invalid items', () => {
    const arr = [
      { severity: 'error', message: 'ok' },
      'not an object',
    ]
    expect(isFindingArray(arr as unknown[])).toBe(false)
  })

  it('returns true when items have extra properties', () => {
    const findings = [
      { severity: 'error', message: 'msg', extra: 'data', count: 5 },
    ]
    expect(isFindingArray(findings)).toBe(true)
  })
})

// ===========================================================================
// formatDurationSeconds Tests
// ===========================================================================

describe('formatDurationSeconds', () => {
  it('formats seconds only when under 60', () => {
    expect(formatDurationSeconds(0)).toBe('0s')
    expect(formatDurationSeconds(1)).toBe('1s')
    expect(formatDurationSeconds(59)).toBe('59s')
  })

  it('formats minutes and seconds when under 60 minutes', () => {
    expect(formatDurationSeconds(60)).toBe('1m 0s')
    expect(formatDurationSeconds(90)).toBe('1m 30s')
    expect(formatDurationSeconds(3599)).toBe('59m 59s')
  })

  it('formats hours and remaining minutes when >= 60 minutes', () => {
    expect(formatDurationSeconds(3600)).toBe('1h 0m')
    expect(formatDurationSeconds(5400)).toBe('1h 30m')
    expect(formatDurationSeconds(7200)).toBe('2h 0m')
    expect(formatDurationSeconds(7260)).toBe('2h 1m')
  })

  it('handles negative numbers by returning 0s', () => {
    expect(formatDurationSeconds(-1)).toBe('0s')
    expect(formatDurationSeconds(-100)).toBe('0s')
  })

  it('handles NaN by returning 0s', () => {
    expect(formatDurationSeconds(NaN)).toBe('0s')
  })

  it('handles Infinity by returning 0s', () => {
    expect(formatDurationSeconds(Infinity)).toBe('0s')
    expect(formatDurationSeconds(-Infinity)).toBe('0s')
  })

  it('handles fractional seconds by flooring', () => {
    expect(formatDurationSeconds(90.7)).toBe('1m 30s')
    expect(formatDurationSeconds(0.9)).toBe('0s')
    expect(formatDurationSeconds(59.99)).toBe('59s')
  })
})

// ===========================================================================
// Additional parseLiveOutput edge cases
// ===========================================================================

describe('parseLiveOutput edge cases', () => {
  it('handles tool_use_result as null', () => {
    const input = JSON.stringify({ tool_use_result: null })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles tool_use_result as a number (not string or object)', () => {
    const input = JSON.stringify({ tool_use_result: 42 })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles tool_use_result object with empty strings', () => {
    const input = JSON.stringify({ tool_use_result: { stdout: '', stderr: '', content: '' } })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles message.content with empty input object', () => {
    const input = JSON.stringify({
      message: { content: [{ input: {} }] },
    })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles message.content with input having non-string values', () => {
    const input = JSON.stringify({
      message: { content: [{ input: { description: 123, file_path: true } }] },
    })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles tool_use_result.file without filePath', () => {
    const input = JSON.stringify({
      tool_use_result: { file: { name: 'foo.ts' } },
    })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles tool_use_result.file.filePath as empty string', () => {
    const input = JSON.stringify({
      tool_use_result: { file: { filePath: '' } },
    })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles message with no content property', () => {
    const input = JSON.stringify({ message: { role: 'assistant' } })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles message.content items with empty strings', () => {
    const input = JSON.stringify({
      message: { content: [{ thinking: '', text: '', content: '' }] },
    })
    expect(parseLiveOutput(input)).toEqual([])
  })

  it('handles very long input strings without performance issues', () => {
    const longText = 'x'.repeat(100_000)
    const input = JSON.stringify({ tool_use_result: { stdout: longText } })
    const result = parseLiveOutput(input)
    expect(result.length).toBeGreaterThan(0)
  })

  it('handles deeply nested large JSON objects', () => {
    const largeContent = Array.from({ length: 200 }, (_, i) => ({
      text: `line ${i}`,
      type: 'text',
    }))
    const input = JSON.stringify({ message: { content: largeContent } })
    const result = parseLiveOutput(input)
    expect(result.length).toBe(200)
  })
})

// ===========================================================================
// Additional isFindingArray edge cases
// ===========================================================================

describe('isFindingArray edge cases', () => {
  it('returns false for array of undefined items', () => {
    expect(isFindingArray([undefined, undefined] as unknown[])).toBe(false)
  })

  it('returns true for single finding with all optional fields', () => {
    const findings = [
      { severity: 'error', message: 'msg', file: 'a.ts', line: 1 },
    ]
    expect(isFindingArray(findings)).toBe(true)
  })

  it('returns false for objects with severity but empty message key missing', () => {
    const arr = [{ severity: 'info', msg: 'wrong key name' }]
    expect(isFindingArray(arr as unknown[])).toBe(false)
  })
})

// ===========================================================================
// Additional toSectionTitle edge cases
// ===========================================================================

describe('toSectionTitle edge cases', () => {
  it('handles all-uppercase key', () => {
    expect(toSectionTitle('SUMMARY')).toBe('SUMMARY')
  })

  it('handles key with numbers', () => {
    expect(toSectionTitle('test_count_v2')).toBe('Test Count V2')
  })

  it('handles key with multiple underscores', () => {
    expect(toSectionTitle('a__b___c')).toBe('A  B   C')
  })
})
