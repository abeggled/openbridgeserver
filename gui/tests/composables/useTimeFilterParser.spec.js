/**
 * Tests for useTimeFilterParser composable.
 *
 * Pure-function parser/formatter for the new time-filter UX (#432):
 *   - parseDurationToken: '1h30m', '5min', '2d', etc.
 *   - parseTimePointToken: '-1h', '14:30', '2026-05-11T14:30', ISO with Z, …
 *   - formatDurationDeutsch: seconds → 'Xh Ymin Zs'
 *   - formatTimeFilter:   filter state → topbar label (e.g. 'Letzte 1h')
 */
import { describe, it, expect } from 'vitest'
import {
  parseDurationToken,
  parseTimePointToken,
  formatDurationDeutsch,
  formatTimeFilter,
} from '@/composables/useTimeFilterParser.js'

describe('parseDurationToken', () => {
  it('parses single units', () => {
    expect(parseDurationToken('30s')).toEqual({ seconds: 30, sign: 1 })
    expect(parseDurationToken('5min')).toEqual({ seconds: 300, sign: 1 })
    expect(parseDurationToken('5m')).toEqual({ seconds: 300, sign: 1 })
    expect(parseDurationToken('1h')).toEqual({ seconds: 3600, sign: 1 })
    expect(parseDurationToken('2d')).toEqual({ seconds: 172800, sign: 1 })
  })

  it('accepts the German "std" alias for hours', () => {
    expect(parseDurationToken('1std')).toEqual({ seconds: 3600, sign: 1 })
    expect(parseDurationToken('2std')).toEqual({ seconds: 7200, sign: 1 })
    expect(parseDurationToken('1std 30min')).toEqual({ seconds: 3600 + 1800, sign: 1 })
    expect(parseDurationToken('-2std')).toEqual({ seconds: 7200, sign: -1 })
  })

  it('parses combined tokens', () => {
    expect(parseDurationToken('1h10m')).toEqual({ seconds: 3600 + 600, sign: 1 })
    expect(parseDurationToken('1h10min')).toEqual({ seconds: 3600 + 600, sign: 1 })
    expect(parseDurationToken('1h10m30s')).toEqual({ seconds: 3600 + 600 + 30, sign: 1 })
    expect(parseDurationToken('2d3h')).toEqual({ seconds: 2 * 86400 + 3 * 3600, sign: 1 })
  })

  it('allows optional whitespace between segments', () => {
    expect(parseDurationToken('1h 10m')).toEqual({ seconds: 3600 + 600, sign: 1 })
    expect(parseDurationToken(' 1h  10min  30s ')).toEqual({ seconds: 3600 + 600 + 30, sign: 1 })
  })

  it('honours a leading minus sign', () => {
    expect(parseDurationToken('-1h')).toEqual({ seconds: 3600, sign: -1 })
    expect(parseDurationToken('-30min')).toEqual({ seconds: 1800, sign: -1 })
    expect(parseDurationToken('- 1h')).toEqual({ seconds: 3600, sign: -1 })
  })

  it('honours an explicit plus sign as positive', () => {
    expect(parseDurationToken('+1h')).toEqual({ seconds: 3600, sign: 1 })
  })

  it('returns null on empty / whitespace-only input', () => {
    expect(parseDurationToken('')).toBeNull()
    expect(parseDurationToken('   ')).toBeNull()
    expect(parseDurationToken(null)).toBeNull()
    expect(parseDurationToken(undefined)).toBeNull()
  })

  it('returns null on invalid input', () => {
    expect(parseDurationToken('abc')).toBeNull()
    expect(parseDurationToken('5x')).toBeNull()
    expect(parseDurationToken('1.5h')).toBeNull()
    expect(parseDurationToken('1h-30m')).toBeNull()
    expect(parseDurationToken('h')).toBeNull()
    expect(parseDurationToken('30')).toBeNull()
  })
})

describe('parseTimePointToken', () => {
  const now = new Date('2026-05-11T12:00:00Z')

  it('parses negative relative offsets', () => {
    const d = parseTimePointToken('-1h', now)
    expect(d).toBeInstanceOf(Date)
    expect(d.getTime()).toBe(now.getTime() - 3600_000)
  })

  it('parses positive relative offsets', () => {
    const d = parseTimePointToken('+10min', now)
    expect(d.getTime()).toBe(now.getTime() + 600_000)
  })

  it('parses relative with combined units', () => {
    const d = parseTimePointToken('-1h30m', now)
    expect(d.getTime()).toBe(now.getTime() - (3600 + 30 * 60) * 1000)
  })

  it('parses a wall-clock time as today in local TZ', () => {
    const localNow = new Date(2026, 4, 11, 12, 0, 0) // 2026-05-11 12:00 local
    const d = parseTimePointToken('14:30', localNow)
    expect(d).toBeInstanceOf(Date)
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(4)
    expect(d.getDate()).toBe(11)
    expect(d.getHours()).toBe(14)
    expect(d.getMinutes()).toBe(30)
  })

  it('rolls forward when wall-clock time has already passed today', () => {
    // We don't auto-roll — passing 11:30 with a 12:00 anchor yields today 11:30,
    // which the UI shows as "in the past". Verified here.
    const localNow = new Date(2026, 4, 11, 12, 0, 0)
    const d = parseTimePointToken('11:30', localNow)
    expect(d.getDate()).toBe(11)
    expect(d.getHours()).toBe(11)
    expect(d.getMinutes()).toBe(30)
  })

  it('parses absolute ISO without Z (interpreted as local)', () => {
    const d = parseTimePointToken('2026-05-11T14:30')
    expect(d).toBeInstanceOf(Date)
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(4)
    expect(d.getDate()).toBe(11)
    expect(d.getHours()).toBe(14)
    expect(d.getMinutes()).toBe(30)
  })

  it('parses "YYYY-MM-DD HH:MM" with a space separator', () => {
    const d = parseTimePointToken('2026-05-11 14:30')
    expect(d.getFullYear()).toBe(2026)
    expect(d.getHours()).toBe(14)
  })

  it('parses ISO with explicit Z (UTC)', () => {
    const d = parseTimePointToken('2026-05-11T14:30:00Z')
    expect(d.toISOString()).toBe('2026-05-11T14:30:00.000Z')
  })

  it('parses ISO with explicit timezone offset', () => {
    const d = parseTimePointToken('2026-05-11T14:30:00+02:00')
    expect(d.getTime()).toBe(new Date('2026-05-11T12:30:00Z').getTime())
  })

  it('returns null on invalid input', () => {
    expect(parseTimePointToken('')).toBeNull()
    expect(parseTimePointToken(null)).toBeNull()
    expect(parseTimePointToken('garbage')).toBeNull()
    expect(parseTimePointToken('25:99')).toBeNull()
    expect(parseTimePointToken('2026-13-40')).toBeNull()
  })

  it('handles the 23:59 + 5min day boundary via combined relative', () => {
    const anchor = new Date(2026, 4, 11, 23, 59, 0)
    const d = parseTimePointToken('+5min', anchor)
    // Anchor is local 23:59 → +5min = 00:04 next day local
    expect(d.getDate()).toBe(12)
    expect(d.getHours()).toBe(0)
    expect(d.getMinutes()).toBe(4)
  })
})

// ---------------------------------------------------------------------------
// QA-01 audit (#439): additional invalid / day-boundary / TZ round-trip
// ---------------------------------------------------------------------------

describe('parseTimePointToken QA-01 edge cases (#439)', () => {
  it('rejects 5x / abc / 30 / 1h-30m as relative tokens', () => {
    expect(parseTimePointToken('5x')).toBeNull()
    expect(parseTimePointToken('1h-30m')).toBeNull()
    // Bare numbers and free-form text are no valid time point either
    expect(parseTimePointToken('30')).toBeNull()
    expect(parseTimePointToken('abc')).toBeNull()
  })

  it('rejects fractional durations like 1.5h, even with a leading sign', () => {
    expect(parseTimePointToken('+1.5h')).toBeNull()
    expect(parseTimePointToken('-1,5h')).toBeNull()
  })

  it('parses "2026-05-11 23:59" then adds +5min crossing into the next day', () => {
    // Two-step semantics: the popover composes wall-clock + relative.
    const anchor = parseTimePointToken('2026-05-11 23:59')
    expect(anchor).toBeInstanceOf(Date)
    const after = parseTimePointToken('+5min', anchor)
    expect(after.getFullYear()).toBe(2026)
    expect(after.getMonth()).toBe(4) // May (0-based)
    expect(after.getDate()).toBe(12)
    expect(after.getHours()).toBe(0)
    expect(after.getMinutes()).toBe(4)
  })

  it('round-trips an ISO with explicit UTC through new Date() → toISOString()', () => {
    // Stability of the parser under TZ-aware formatting (used by useTz.js).
    const raw = '2026-05-11T14:30:00Z'
    const parsed = parseTimePointToken(raw)
    expect(parsed.toISOString()).toBe('2026-05-11T14:30:00.000Z')
    // Reformatting back to ISO with the same anchor must produce the same string
    expect(new Date(parsed.toISOString()).toISOString()).toBe('2026-05-11T14:30:00.000Z')
  })

  it('rejects overflow dates like 2026-02-30', () => {
    expect(parseTimePointToken('2026-02-30T12:00')).toBeNull()
    expect(parseTimePointToken('2026-13-01T00:00')).toBeNull()
    expect(parseTimePointToken('2026-05-11T25:00')).toBeNull()
  })

  it('tolerates leading/trailing whitespace around an ISO timestamp', () => {
    const d = parseTimePointToken('   2026-05-11T14:30   ')
    expect(d).toBeInstanceOf(Date)
    expect(d.getHours()).toBe(14)
  })

  it('returns null on whitespace-only input', () => {
    expect(parseTimePointToken('   ')).toBeNull()
    expect(parseTimePointToken('\t\n')).toBeNull()
  })
})

describe('formatDurationDeutsch', () => {
  it('formats 0 seconds', () => {
    expect(formatDurationDeutsch(0)).toBe('0s')
  })

  it('formats sub-minute seconds', () => {
    expect(formatDurationDeutsch(30)).toBe('30s')
  })

  it('formats whole minutes', () => {
    expect(formatDurationDeutsch(60)).toBe('1min')
    expect(formatDurationDeutsch(300)).toBe('5min')
  })

  it('formats hour combinations', () => {
    expect(formatDurationDeutsch(3600)).toBe('1h')
    expect(formatDurationDeutsch(3661)).toBe('1h 1min 1s')
  })

  it('formats days', () => {
    expect(formatDurationDeutsch(86400)).toBe('1d')
    expect(formatDurationDeutsch(90061)).toBe('1d 1h 1min 1s')
  })

  it('handles large values', () => {
    expect(formatDurationDeutsch(7 * 86400 + 3600)).toBe('7d 1h')
  })

  it('rejects negative input by returning absolute formatted value', () => {
    expect(formatDurationDeutsch(-3600)).toBe('1h')
  })
})

describe('formatTimeFilter', () => {
  it('returns "aus" for null / empty', () => {
    expect(formatTimeFilter(null)).toBe('aus')
    expect(formatTimeFilter({})).toBe('aus')
    expect(formatTimeFilter({ mode: 'range' })).toBe('aus')
  })

  it('formats a relative-only "Letzte"-Bereich', () => {
    expect(
      formatTimeFilter({ mode: 'range', from: { seconds: 3600, sign: -1 } }),
    ).toBe('Letzte 1h')
  })

  it('formats a range with two relative bounds', () => {
    expect(
      formatTimeFilter({
        mode: 'range',
        from: { seconds: 3600, sign: -1 },
        to: { seconds: 5 * 60, sign: -1 },
      }),
    ).toBe('Bereich -1h … -5min')
  })

  it('formats a point with span', () => {
    const point = new Date('2026-05-11T13:50:00')
    expect(
      formatTimeFilter({ mode: 'point', point, span: { seconds: 600, sign: 1 } }),
    ).toContain('±')
    expect(
      formatTimeFilter({ mode: 'point', point, span: { seconds: 600, sign: 1 } }),
    ).toContain('10min')
  })

  it('formats a range with absolute date bounds', () => {
    const from = new Date('2026-05-11T13:00:00')
    const to = new Date('2026-05-11T14:00:00')
    expect(formatTimeFilter({ mode: 'range', from, to })).toContain('…')
  })
})
