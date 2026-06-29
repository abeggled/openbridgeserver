import { describe, it, expect, beforeEach } from 'vitest'
import { useSettingsStore } from '@/stores/settings'
import { useTz } from '@/composables/useTz'

// Pin timezone to UTC so date assertions are locale-independent
function setup() {
  const settings = useSettingsStore()
  settings.timezone = 'UTC'
  return useTz()
}

describe('toUtcDate', () => {
  it('returns null for null input', () => {
    const { toUtcDate } = setup()
    expect(toUtcDate(null)).toBeNull()
  })

  it('returns null for empty string', () => {
    const { toUtcDate } = setup()
    expect(toUtcDate('')).toBeNull()
  })

  it('parses a numeric millisecond timestamp', () => {
    const { toUtcDate } = setup()
    const d = toUtcDate(0)
    expect(d).toBeInstanceOf(Date)
    expect(d.getTime()).toBe(0)
  })

  it('parses a numeric string as milliseconds', () => {
    const { toUtcDate } = setup()
    const d = toUtcDate('1000')
    expect(d.getTime()).toBe(1000)
  })

  it('parses ISO string with Z as UTC', () => {
    const { toUtcDate } = setup()
    const d = toUtcDate('2024-06-15T12:00:00Z')
    expect(d.getUTCFullYear()).toBe(2024)
    expect(d.getUTCHours()).toBe(12)
  })

  it('appends Z to ISO string without timezone marker', () => {
    const { toUtcDate } = setup()
    const d = toUtcDate('2024-06-15T12:00:00')
    expect(d.getUTCFullYear()).toBe(2024)
    expect(d.getUTCHours()).toBe(12)
  })

  it('parses ISO string with explicit +HH:MM offset', () => {
    const { toUtcDate } = setup()
    const d = toUtcDate('2024-06-15T14:00:00+02:00')
    expect(d.getUTCHours()).toBe(12) // 14:00 +02:00 = 12:00 UTC
  })
})

describe('fmtDate', () => {
  it('returns "—" for null', () => {
    const { fmtDate } = setup()
    expect(fmtDate(null)).toBe('—')
  })

  it('formats an ISO date string (de-CH locale, UTC tz)', () => {
    const { fmtDate } = setup()
    const result = fmtDate('2024-06-15T00:00:00Z')
    expect(result).toContain('2024')
    expect(result).toContain('06')
    expect(result).toContain('15')
  })
})

describe('fmtDateTime', () => {
  it('returns "—" for null', () => {
    const { fmtDateTime } = setup()
    expect(fmtDateTime(null)).toBe('—')
  })

  it('formats an ISO datetime string (de-CH locale, UTC tz)', () => {
    const { fmtDateTime } = setup()
    const result = fmtDateTime('2024-06-15T12:30:00Z')
    expect(result).toContain('2024')
    expect(result).toContain('12')
    expect(result).toContain('30')
  })
})

describe('fmtChartLabel', () => {
  it('returns "" for null', () => {
    const { fmtChartLabel } = setup()
    expect(fmtChartLabel(null)).toBe('')
  })

  it('returns a formatted label string', () => {
    const { fmtChartLabel } = setup()
    const result = fmtChartLabel('2024-06-15T12:30:00Z')
    expect(result).toBeTruthy()
    expect(result).toContain('15')
  })
})

describe('toDatetimeLocal', () => {
  it('formats a Date as YYYY-MM-DDTHH:MM', () => {
    const { toDatetimeLocal } = setup()
    const d = new Date('2024-06-15T12:30:00')
    const result = toDatetimeLocal(d)
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(result).toContain('2024')
  })

  it('accepts a timestamp number', () => {
    const { toDatetimeLocal } = setup()
    const result = toDatetimeLocal(new Date('2024-01-01T00:00:00').getTime())
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
  })
})

describe('fromDatetimeLocal', () => {
  it('returns null for empty string', () => {
    const { fromDatetimeLocal } = setup()
    expect(fromDatetimeLocal('')).toBeNull()
  })

  it('converts a datetime-local string to ISO', () => {
    const { fromDatetimeLocal } = setup()
    const result = fromDatetimeLocal('2024-06-15T12:30')
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
  })
})
