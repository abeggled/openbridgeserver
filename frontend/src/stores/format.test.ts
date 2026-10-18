import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { displaySettings } from '@/api/client'
import { useFormatStore } from './format'

vi.mock('@/api/client', () => ({
  displaySettings: { get: vi.fn() },
}))

const NBSP = '\u00A0'
const getMock = vi.mocked(displaySettings.get)

function payload(overrides: Record<string, unknown> = {}) {
  return {
    language: 'de',
    timezone: 'Europe/Zurich',
    date_format: 'dd.MM.yyyy',
    time_format: 'HH:mm:ss',
    region_format: 'auto',
    currency: 'auto',
    resolved_region_format: 'de-DE',
    resolved_currency: 'EUR',
    supported_region_formats: ['auto', 'de-DE', 'de-CH'],
    supported_currencies: ['auto', 'EUR', 'CHF'],
    ...overrides,
  }
}

describe('useFormatStore (#1073)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getMock.mockReset()
  })

  it('starts on the German default before the settings are loaded', () => {
    const store = useFormatStore()

    expect(store.loaded).toBe(false)
    expect(store.regionFormat).toBe('de-DE')
    expect(store.currency).toBe('EUR')
    expect(store.fmtNumber(1.05, { decimals: 3 })).toBe('1,050')
  })

  it('applies the resolved regional format from the public endpoint', async () => {
    getMock.mockResolvedValue(payload({ resolved_region_format: 'de-CH', resolved_currency: 'CHF' }))
    const store = useFormatStore()

    await store.load()

    expect(store.loaded).toBe(true)
    expect(store.regionFormat).toBe('de-CH')
    expect(store.currency).toBe('CHF')
    expect(store.fmtNumber(1.05, { decimals: 3 })).toBe('1.050')
    expect(store.fmtCurrency(1234.5)).toBe(`CHF${NBSP}1'234.50`)
    expect(store.fmtPercent(12.34)).toBe('12.3\u202F%')
  })

  it('keeps the format independent of the UI language', async () => {
    getMock.mockResolvedValue(payload({ language: 'en', resolved_region_format: 'de-DE', resolved_currency: 'EUR' }))
    const store = useFormatStore()

    await store.load()

    expect(store.language).toBe('en')
    expect(store.regionFormat).toBe('de-DE')
    expect(store.fmtNumber(1.05, { decimals: 3 })).toBe('1,050')
  })

  it('falls back to the unresolved setting when the server omits the resolved fields', async () => {
    getMock.mockResolvedValue(payload({
      language: '',
      region_format: 'en-GB',
      currency: 'GBP',
      resolved_region_format: '',
      resolved_currency: '',
      timezone: '',
    }))
    const store = useFormatStore()

    await store.load()

    expect(store.language).toBe('de')
    expect(store.regionFormat).toBe('en-GB')
    expect(store.currency).toBe('GBP')
    expect(store.timezone).toBeNull()
  })

  it('stays usable with the default format when the endpoint is unreachable', async () => {
    getMock.mockRejectedValue(new Error('offline'))
    const store = useFormatStore()

    await store.load()

    expect(store.loaded).toBe(true)
    expect(store.regionFormat).toBe('de-DE')
    expect(store.fmtNumber(1234.5, { decimals: 1 })).toBe('1.234,5')
  })

  describe('fmtDateTime', () => {
    it('formats in the configured timezone and regional format', async () => {
      getMock.mockResolvedValue(payload({ resolved_region_format: 'de-CH', timezone: 'UTC' }))
      const store = useFormatStore()
      await store.load()

      const text = store.fmtDateTime('2026-06-08T14:05:00Z', { hour: '2-digit', minute: '2-digit' })

      expect(text).toBe('14:05')
    })

    it('renders date and time with the configured patterns when no options are given', async () => {
      getMock.mockResolvedValue(payload({ resolved_region_format: 'de-DE', timezone: 'UTC' }))
      const store = useFormatStore()
      await store.load()

      expect(store.fmtDateTime('2026-06-12T14:30:45Z')).toBe('12.06.2026 14:30:45')
    })

    it('honours administrator-configured date and time patterns (#1073)', async () => {
      getMock.mockResolvedValue(payload({
        timezone: 'UTC',
        date_format: 'yyyy/MM/dd',
        time_format: 'HH-mm',
        resolved_region_format: 'en-GB',
      }))
      const store = useFormatStore()
      await store.load()

      expect(store.fmtDate('2026-06-08T14:05:00Z')).toBe('2026/06/08')
      expect(store.fmtTime('2026-06-08T14:05:00Z')).toBe('14-05')
      expect(store.fmtDateTime('2026-06-08T14:05:00Z')).toBe('2026/06/08 14-05')
    })

    it.each([
      ['de', 'Montag, 8. Juni 2026'],
      ['en', 'Monday, 8. June 2026'],
      ['fr', 'lundi, 8. juin 2026'],
      ['es', 'lunes, 8. junio 2026'],
      ['it', 'lunedì, 8. giugno 2026'],
      ['gsw', 'Mäntig, 8. Juni 2026'],
      ['xx', 'Monday, 8. June 2026'],  // unknown language falls back to English
    ])('takes weekday and month names from the %s UI language, not the region', async (uiLanguage, expected) => {
      getMock.mockResolvedValue(payload({
        timezone: 'UTC',
        date_format: 'EEEE, d. MMMM yyyy',
        // An explicitly Swiss/US region must not anglicise or germanise the names.
        resolved_region_format: 'en-US',
      }))
      const store = useFormatStore()
      store.setUiLanguage(uiLanguage)
      await store.load()

      expect(store.fmtDate('2026-06-08T12:00:00Z')).toBe(expected)
    })

    it('applies the configured patterns in the server timezone', async () => {
      getMock.mockResolvedValue(payload({ timezone: 'Asia/Tokyo', date_format: 'dd.MM.yyyy', time_format: 'HH:mm' }))
      const store = useFormatStore()
      await store.load()

      // 23:30 UTC is the next day in Tokyo.
      expect(store.fmtDateTime('2026-06-08T23:30:00Z')).toBe('09.06.2026 08:30')
    })

    it('returns an empty string from the pattern path for an unparsable value', async () => {
      const store = useFormatStore()

      expect(store.fmtDate('not a date')).toBe('')
      expect(store.fmtTime('not a date')).toBe('')
      expect(store.fmtDateTime('not a date')).toBe('')
    })

    it('accepts Date and epoch-millisecond input', () => {
      const store = useFormatStore()
      const options: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'UTC' }

      const fromDate = store.fmtDateTime(new Date('2026-06-08T00:00:00Z'), options)
      const fromMs = store.fmtDateTime(Date.UTC(2026, 5, 8), options)

      expect(fromDate).toBe('08.06.2026')
      expect(fromMs).toBe(fromDate)
    })

    it('returns an empty string for an unparsable timestamp', () => {
      const store = useFormatStore()

      expect(store.fmtDateTime('not a date')).toBe('')
    })

    it('falls back to the default locale but keeps the configured timezone', async () => {
      getMock.mockResolvedValue(payload({ resolved_region_format: 'not a locale', timezone: 'UTC' }))
      const store = useFormatStore()
      await store.load()

      expect(store.fmtDateTime('2026-06-08T23:30:00Z', { hour: '2-digit', minute: '2-digit' })).toBe('23:30')
    })

    it('lets an explicit timeZone option win over the configured one', async () => {
      getMock.mockResolvedValue(payload({ resolved_region_format: 'de-DE', timezone: 'Asia/Tokyo' }))
      const store = useFormatStore()
      await store.load()

      const text = store.fmtDateTime('2026-06-08T23:30:00Z', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })

      expect(text).toBe('23:30')
    })

    it('drops an unusable configured timezone rather than throwing', async () => {
      getMock.mockResolvedValue(payload({ resolved_region_format: 'de-DE', timezone: 'Not/A_Zone' }))
      const store = useFormatStore()
      await store.load()

      // No timeZone option — the broken configured zone is the only candidate.
      expect(store.fmtDateTime('2026-06-08T12:00:00Z', { year: 'numeric' })).toBe('2026')
    })
  })
})
