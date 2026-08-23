import { describe, expect, it } from 'vitest'
import { formatPattern, toUtcDate, type DateTimeNames } from './datePattern'

const DE: DateTimeNames = {
  weekdays: 'Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag',
  weekdaysShort: 'Mo.|Di.|Mi.|Do.|Fr.|Sa.|So.',
  weekdaysTwo: 'Mo|Di|Mi|Do|Fr|Sa|So',
  months: 'Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember',
  monthsShort: 'Jan.|Feb.|März|Apr.|Mai|Juni|Juli|Aug.|Sept.|Okt.|Nov.|Dez.',
}

// Monday, 8 June 2026, 04:05:09 UTC — single-digit day/hour/minute/second on purpose.
const SUBJECT = new Date('2026-06-08T04:05:09Z')

describe('formatPattern (#1073)', () => {
  it.each([
    ['dd.MM.yyyy', '08.06.2026'],
    ['yyyy-MM-dd', '2026-06-08'],
    ['d.M.yy', '8.6.26'],
    ['HH:mm:ss', '04:05:09'],
    ['H:m:s', '4:5:9'],
    ['HH-mm', '04-05'],
    ['EEEE, d. MMMM yyyy', 'Montag, 8. Juni 2026'],
    ['EEE d. MMM yy', 'Mo. 8. Juni 26'],
    ['EE', 'Mo'],
  ])('renders %s', (pattern, expected) => {
    expect(formatPattern(SUBJECT, pattern, 'UTC', DE)).toBe(expected)
  })

  it('keeps literal separators inside a tokenized word', () => {
    expect(formatPattern(SUBJECT, 'yyyy-MM-ddTHH:mm', 'UTC', DE)).toBe('2026-06-08T04:05')
  })

  it('leaves words that are not patterns verbatim', () => {
    expect(formatPattern(SUBJECT, 'Stand: dd.MM.yyyy', 'UTC', DE)).toBe('Stand: 08.06.2026')
  })

  it('renders in the given timezone', () => {
    expect(formatPattern(SUBJECT, 'dd.MM.yyyy HH:mm', 'Asia/Tokyo', DE)).toBe('08.06.2026 13:05')
    expect(formatPattern(new Date('2026-06-08T23:30:00Z'), 'dd.MM.yyyy', 'Asia/Tokyo', DE)).toBe('09.06.2026')
  })

  it('falls back to the host timezone when the configured one is unusable', () => {
    expect(formatPattern(SUBJECT, 'yyyy', 'Not/A_Zone', DE)).toBe('2026')
  })

  it('accepts a null timezone', () => {
    expect(formatPattern(SUBJECT, 'yyyy', null, DE)).toBe('2026')
  })
})

describe('toUtcDate (#1073)', () => {
  it('parses ISO strings with and without a zone', () => {
    expect(toUtcDate('2026-06-08T04:05:09Z')?.toISOString()).toBe('2026-06-08T04:05:09.000Z')
    // Zone-less timestamps (SQLite aggregate buckets) are UTC, not local time.
    expect(toUtcDate('2026-06-08T04:05:09')?.toISOString()).toBe('2026-06-08T04:05:09.000Z')
    expect(toUtcDate('2026-06-08T04:05:09+02:00')?.toISOString()).toBe('2026-06-08T02:05:09.000Z')
  })

  it('accepts epoch milliseconds as number and string', () => {
    const ms = Date.UTC(2026, 5, 8)
    expect(toUtcDate(ms)?.toISOString()).toBe('2026-06-08T00:00:00.000Z')
    expect(toUtcDate(String(ms))?.toISOString()).toBe('2026-06-08T00:00:00.000Z')
  })

  it('passes a Date through and rejects an invalid one', () => {
    expect(toUtcDate(SUBJECT)).toBe(SUBJECT)
    expect(toUtcDate(new Date('nope'))).toBeNull()
  })

  it.each([[null], [undefined], [''], ['not a date']])('returns null for %p', (input) => {
    expect(toUtcDate(input)).toBeNull()
  })
})
