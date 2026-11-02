import { describe, expect, it } from 'vitest'
import {
  addDays,
  buildDays,
  clampDays,
  DEFAULT_DAYS,
  isoDateIn,
  MAX_DAYS,
  MIN_DAYS,
  parseEvents,
  type CalendarEvent,
} from './calendarEvents'

const ROW_TIMED = ['2026-10-30', '10:00', '11:00', 'Zahnarzt; Kontrolle', 'Bern', 'Beschreibung']
const ROW_ALLDAY = ['2026-10-31', '', '', 'Papiersammlung "Kartons"', '', '']

function ev(date: string, start: string, summary: string): CalendarEvent {
  return { date, start, end: '', summary, location: '' }
}

describe('parseEvents', () => {
  it('reads the rows the iCalendar block emits', () => {
    expect(parseEvents([ROW_TIMED, ROW_ALLDAY])).toEqual([
      { date: '2026-10-30', start: '10:00', end: '11:00', summary: 'Zahnarzt; Kontrolle', location: 'Bern' },
      { date: '2026-10-31', start: '', end: '', summary: 'Papiersammlung "Kartons"', location: '' },
    ])
  })

  it('accepts the same array as a JSON string', () => {
    expect(parseEvents(JSON.stringify([ROW_TIMED]))).toHaveLength(1)
  })

  it('returns nothing for invalid JSON, non-arrays and missing values', () => {
    expect(parseEvents('not json')).toEqual([])
    expect(parseEvents('{"a": 1}')).toEqual([])
    expect(parseEvents(null)).toEqual([])
    expect(parseEvents(undefined)).toEqual([])
    expect(parseEvents(42)).toEqual([])
  })

  it('drops rows without a usable date and blanks non-text fields', () => {
    const rows = [
      'nope',
      [],
      [20261030, '10:00'],
      ['30.10.2026', '10:00', '', 'falsches Datum'],
      ['2026-10-30', 10, null, { x: 1 }, undefined],
    ]
    expect(parseEvents(rows)).toEqual([{ date: '2026-10-30', start: '', end: '', summary: '', location: '' }])
  })
})

describe('clampDays', () => {
  it('keeps values in range and truncates fractions', () => {
    expect(clampDays(14)).toBe(14)
    expect(clampDays('3')).toBe(3)
    expect(clampDays(2.9)).toBe(2)
  })

  it('clamps to the allowed range', () => {
    expect(clampDays(0)).toBe(MIN_DAYS)
    expect(clampDays(-5)).toBe(MIN_DAYS)
    expect(clampDays(10_000)).toBe(MAX_DAYS)
  })

  it('falls back to the default for non-numbers', () => {
    expect(clampDays(undefined)).toBe(DEFAULT_DAYS)
    expect(clampDays('abc')).toBe(DEFAULT_DAYS)
  })
})

describe('isoDateIn', () => {
  // 23:30 UTC is already the next day in Zurich (UTC+1 in winter).
  const lateEvening = new Date('2026-12-31T23:30:00Z')

  it('uses the given timezone', () => {
    expect(isoDateIn(lateEvening, 'Europe/Zurich')).toBe('2027-01-01')
    expect(isoDateIn(lateEvening, 'UTC')).toBe('2026-12-31')
  })

  it('falls back to the browser zone without or with an unusable timezone', () => {
    const local = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(lateEvening)
    expect(isoDateIn(lateEvening, null)).toBe(local)
    expect(isoDateIn(lateEvening, 'Not/AZone')).toBe(local)
  })
})

describe('addDays', () => {
  it('crosses month, year and DST boundaries by whole days', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2026-10-24', 2)).toBe('2026-10-26')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })
})

describe('buildDays', () => {
  const today = '2026-10-29'

  it('marks today and tomorrow and leaves out empty days by default', () => {
    const groups = buildDays(
      [{ events: [ev('2026-10-29', '', 'A'), ev('2026-10-30', '', 'B'), ev('2026-11-02', '', 'C')], color: '#f00', label: 'X' }],
      today,
      7,
      false,
    )
    expect(groups.map((g) => [g.date, g.kind])).toEqual([
      ['2026-10-29', 'today'],
      ['2026-10-30', 'tomorrow'],
      ['2026-11-02', 'date'],
    ])
    expect(groups[0].events[0]).toMatchObject({ summary: 'A', color: '#f00', sourceLabel: 'X', sourceIndex: 0 })
  })

  it('keeps empty days when asked to', () => {
    const groups = buildDays([], today, 3, true)
    expect(groups.map((g) => [g.date, g.events.length])).toEqual([
      ['2026-10-29', 0],
      ['2026-10-30', 0],
      ['2026-10-31', 0],
    ])
  })

  it('ignores events before today and beyond the window', () => {
    const groups = buildDays(
      [{ events: [ev('2026-10-28', '', 'past'), ev('2026-10-31', '', 'last'), ev('2026-11-01', '', 'beyond')], color: '', label: '' }],
      today,
      3,
      false,
    )
    expect(groups.flatMap((g) => g.events.map((e) => e.summary))).toEqual(['last'])
  })

  it('merges sources: all-day first, then by start time, then by source order', () => {
    const groups = buildDays(
      [
        { events: [ev(today, '18:00', 'S0 late'), ev(today, '09:00', 'S0 same time')], color: 'green', label: 'Privat' },
        { events: [ev(today, '09:00', 'S1 same time'), ev(today, '', 'S1 all-day'), ev(today, '07:30', 'S1 early')], color: 'orange', label: 'Abfuhr' },
        { events: [ev(today, '', 'S2 all-day')], color: 'blue', label: 'Schule' },
      ],
      today,
      1,
      false,
    )
    expect(groups[0].events.map((e) => e.summary)).toEqual([
      'S1 all-day',
      'S2 all-day',
      'S1 early',
      'S0 same time',
      'S1 same time',
      'S0 late',
    ])
  })
})
