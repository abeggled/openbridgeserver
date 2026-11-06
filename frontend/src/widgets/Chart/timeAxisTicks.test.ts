import { describe, expect, it } from 'vitest'
import {
  TIME_TICK_STEPS,
  calendarTickLabel,
  calendarTicks,
  chooseTimeTickStep,
  maxTicksForWidth,
  zonedParts,
  zonedToUtc,
  type CalendarTick,
  type TimeTickStep,
} from './timeAxisTicks'

const ZURICH = 'Europe/Zurich'
const HOUR = 3_600_000
const DAY = 24 * HOUR

function step(unit: TimeTickStep['unit'], count: number): TimeTickStep {
  const found = TIME_TICK_STEPS.find(s => s.unit === unit && s.count === count)
  expect(found).toBeDefined()
  return found!
}

/** Local wall-clock text of each tick, e.g. `2026-10-28 00:00`. */
function localTimes(ticks: CalendarTick[], timeZone: string | null): string[] {
  return ticks.map(t => {
    const p = zonedParts(t.value, timeZone)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`
  })
}

describe('maxTicksForWidth', () => {
  it('allows one label per 80 px', () => {
    expect(maxTicksForWidth(1200)).toBe(15)
    expect(maxTicksForWidth(400, 100)).toBe(4)
  })

  it('keeps at least two ticks for a narrow or unmeasured chart', () => {
    expect(maxTicksForWidth(100)).toBe(2)
    expect(maxTicksForWidth(0)).toBe(2)
    expect(maxTicksForWidth(Number.NaN)).toBe(2)
  })
})

describe('chooseTimeTickStep', () => {
  it('picks 12 h for 7 days on a wide chart and 2 days on a narrow one', () => {
    expect(chooseTimeTickStep(7 * DAY, 15)).toMatchObject({ unit: 'hour', count: 12 })
    expect(chooseTimeTickStep(7 * DAY, 5)).toMatchObject({ unit: 'day', count: 2 })
  })

  it('picks minutes for short ranges', () => {
    expect(chooseTimeTickStep(5 * 60_000, 6)).toMatchObject({ unit: 'minute', count: 1 })
    expect(chooseTimeTickStep(HOUR, 6)).toMatchObject({ unit: 'minute', count: 10 })
  })

  it('falls back to the largest step for a range beyond the ladder', () => {
    expect(chooseTimeTickStep(100 * 365 * DAY, 2)).toMatchObject({ unit: 'month', count: 12 })
  })

  it('treats a zero tick budget like a budget of one', () => {
    expect(chooseTimeTickStep(DAY, 0)).toMatchObject({ unit: 'day', count: 1 })
  })
})

describe('zonedToUtc', () => {
  it('resolves an ordinary wall-clock time', () => {
    expect(zonedToUtc(2026, 11, 2, 7 * 60, ZURICH)).toBe(Date.UTC(2026, 10, 2, 6))
  })

  it('shifts a time skipped by spring-forward past the gap', () => {
    // 02:30 does not exist on 2026-03-29 in Zurich; 03:30 CEST = 01:30 UTC
    expect(zonedToUtc(2026, 3, 29, 2 * 60 + 30, ZURICH)).toBe(Date.UTC(2026, 2, 29, 1, 30))
  })

  it('resolves a time whose first guess lands across the fall-back switch', () => {
    // 01:30 CEST on 2026-10-25 = 23:30 UTC the day before
    expect(zonedToUtc(2026, 10, 25, 90, ZURICH)).toBe(Date.UTC(2026, 9, 24, 23, 30))
  })

  it('uses the browser zone without a configured timezone', () => {
    const ms = zonedToUtc(2026, 11, 2, 0, null)
    expect(zonedParts(ms, null)).toMatchObject({ year: 2026, month: 11, day: 2, hour: 0, minute: 0 })
  })

  it('falls back to the browser zone for an unusable timezone', () => {
    expect(zonedParts(0, 'Not/AZone')).toEqual(zonedParts(0, null))
  })
})

describe('calendarTicks', () => {
  it('puts the 7-day ticks of the issue on midnight and noon (#1430)', () => {
    // Screenshot range: 10/27 21:47 → 11/03 21:47 CET
    const from = Date.UTC(2026, 9, 27, 20, 47)
    const to = from + 7 * DAY
    const ticks = calendarTicks(from, to, step('hour', 12), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-10-28 00:00', '2026-10-28 12:00',
      '2026-10-29 00:00', '2026-10-29 12:00',
      '2026-10-30 00:00', '2026-10-30 12:00',
      '2026-10-31 00:00', '2026-10-31 12:00',
      '2026-11-01 00:00', '2026-11-01 12:00',
      '2026-11-02 00:00', '2026-11-02 12:00',
      '2026-11-03 00:00', '2026-11-03 12:00',
    ])
    expect(ticks.map(t => t.kind)).toEqual(Array.from({ length: 7 }, () => ['day', 'time']).flat())
    expect(ticks.map(t => t.major)).toEqual(Array.from({ length: 7 }, () => [true, false]).flat())
    expect(ticks[0]).toMatchObject({ year: 2026, month: 10, weekday: 2 }) // Wednesday
  })

  it('does not add the range bounds as ticks', () => {
    const from = Date.UTC(2026, 10, 3, 8, 47)
    const ticks = calendarTicks(from, from + HOUR, step('minute', 15), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-11-03 10:00', '2026-11-03 10:15', '2026-11-03 10:30', '2026-11-03 10:45',
    ])
  })

  it('keeps hourly ticks on full local hours in a half-hour zone', () => {
    const from = Date.UTC(2026, 10, 3, 0, 0)
    const ticks = calendarTicks(from, from + 3 * HOUR, step('hour', 1), 'Asia/Kolkata')

    expect(ticks.map(t => new Date(t.value).getUTCMinutes())).toEqual([30, 30, 30])
    expect(localTimes(ticks, 'Asia/Kolkata')).toEqual(['2026-11-03 06:00', '2026-11-03 07:00', '2026-11-03 08:00'])
  })

  it('skips the missing hour on spring-forward without doubling a tick', () => {
    const from = Date.UTC(2026, 2, 28, 23, 0) // 00:00 CET
    const ticks = calendarTicks(from, from + 5 * HOUR, step('hour', 1), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-03-29 00:00', '2026-03-29 01:00', '2026-03-29 03:00', '2026-03-29 04:00', '2026-03-29 05:00', '2026-03-29 06:00',
    ])
  })

  it('stays on local midnight across the fall-back day', () => {
    const from = Date.UTC(2026, 9, 23, 12)
    const ticks = calendarTicks(from, from + 4 * DAY, step('day', 1), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-10-24 00:00', '2026-10-25 00:00', '2026-10-26 00:00', '2026-10-27 00:00',
    ])
    expect(ticks[2].value - ticks[1].value).toBe(25 * HOUR)
    expect(ticks.every(t => t.kind === 'day' && !t.major)).toBe(true)
  })

  it('marks the first of the month as major on day steps', () => {
    const from = Date.UTC(2026, 9, 30, 12)
    const ticks = calendarTicks(from, from + 3 * DAY, step('day', 1), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual(['2026-10-31 00:00', '2026-11-01 00:00', '2026-11-02 00:00'])
    expect(ticks.map(t => t.major)).toEqual([false, true, false])
  })

  it('places 2-day ticks on every other day, stable across reloads', () => {
    const from = Date.UTC(2026, 9, 27, 20, 47)
    const later = from + 5 * HOUR
    const ticks = calendarTicks(from, from + 7 * DAY, step('day', 2), ZURICH)
    const shifted = calendarTicks(later, later + 7 * DAY, step('day', 2), ZURICH)

    expect(ticks).toHaveLength(4)
    expect(ticks.every((t, i) => i === 0 || t.value - ticks[i - 1].value === 2 * DAY)).toBe(true)
    // A later range start keeps the same days instead of re-anchoring the grid
    const overlap = (t: CalendarTick) => t.value >= later && t.value <= from + 7 * DAY
    expect(shifted.filter(overlap).map(t => t.value)).toEqual(ticks.filter(overlap).map(t => t.value))
  })

  it('places week ticks on Mondays', () => {
    const from = Date.UTC(2026, 9, 1)
    const ticks = calendarTicks(from, from + 30 * DAY, step('week', 1), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-10-05 00:00', '2026-10-12 00:00', '2026-10-19 00:00', '2026-10-26 00:00',
    ])
    expect(ticks.every(t => t.weekday === 0)).toBe(true)
  })

  it('places quarter ticks on the first of Jan/Apr/Jul/Oct across a year change', () => {
    const from = Date.UTC(2026, 4, 15)
    const ticks = calendarTicks(from, Date.UTC(2027, 7, 1), step('month', 3), ZURICH)

    expect(localTimes(ticks, ZURICH)).toEqual([
      '2026-07-01 00:00', '2026-10-01 00:00', '2027-01-01 00:00', '2027-04-01 00:00', '2027-07-01 00:00',
    ])
    expect(ticks.map(t => t.kind)).toEqual(['month', 'month', 'month', 'month', 'month'])
    expect(ticks.map(t => t.major)).toEqual([false, false, true, false, false])
  })

  it('returns no ticks for an invalid or reversed range', () => {
    expect(calendarTicks(Number.NaN, 0, step('hour', 1), ZURICH)).toEqual([])
    expect(calendarTicks(0, Number.POSITIVE_INFINITY, step('hour', 1), ZURICH)).toEqual([])
    expect(calendarTicks(1000, 0, step('hour', 1), ZURICH)).toEqual([])
  })
})

describe('calendarTickLabel', () => {
  const names = {
    weekdays: 'Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag',
    weekdaysShort: 'Mo.|Di.|Mi.|Do.|Fr.|Sa.|So.',
    weekdaysTwo: 'Mo|Di|Mi|Do|Fr|Sa|So',
    months: 'Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember',
    monthsShort: 'Jan.|Feb.|März|Apr.|Mai|Juni|Juli|Aug.|Sept.|Okt.|Nov.|Dez.',
  }
  const fmt = { time: () => '12:00', dayMonth: () => '02.11.', names }
  const base: CalendarTick = { value: 0, major: false, kind: 'time', year: 2026, month: 11, weekday: 0 }

  it('labels intraday ticks with the clock time', () => {
    expect(calendarTickLabel(base, fmt)).toBe('12:00')
  })

  it('labels day ticks with weekday and date', () => {
    expect(calendarTickLabel({ ...base, kind: 'day' }, fmt)).toBe('Mo 02.11.')
  })

  it('labels month ticks with month name and year', () => {
    expect(calendarTickLabel({ ...base, kind: 'month' }, fmt)).toBe('Nov. 2026')
  })

  it('falls back when a name list is incomplete', () => {
    const sparse = { ...fmt, names: { ...names, weekdaysTwo: '', monthsShort: '' } }
    expect(calendarTickLabel({ ...base, kind: 'day', weekday: 3 }, sparse)).toBe('02.11.')
    expect(calendarTickLabel({ ...base, kind: 'month', month: 5 }, sparse)).toBe('5 2026')
  })
})
