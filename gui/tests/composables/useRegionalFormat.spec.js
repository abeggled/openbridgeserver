import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { resolveCurrency, resolveRegionFormat, useRegionalFormat } from '@/composables/useRegionalFormat'
import { useSettingsStore } from '@/stores/settings'

const NBSP = '\u00A0'
const NARROW_NBSP = '\u202F'

describe('useRegionalFormat (#1073)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  describe('resolveRegionFormat', () => {
    it.each([
      ['de-CH', 'de', 'de-CH'],
      ['de-CH', 'en', 'de-CH'],
      ['auto', 'de', 'de-DE'],
      ['auto', 'gsw', 'de-CH'],
      ['auto', 'en', 'en-US'],
      ['auto', 'fr', 'fr-FR'],
      ['auto', 'it', 'it-IT'],
      ['auto', 'es', 'es-ES'],
      ['auto', 'xx', 'de-DE'],
      [undefined, undefined, 'de-DE'],
    ])('resolves %s / %s to %s', (regionFormat, language, expected) => {
      expect(resolveRegionFormat(regionFormat, language)).toBe(expected)
    })
  })

  describe('resolveCurrency', () => {
    it.each([
      ['CHF', 'de-DE', 'CHF'],
      ['auto', 'de-CH', 'CHF'],
      ['auto', 'fr-CH', 'CHF'],
      ['auto', 'it-CH', 'CHF'],
      ['auto', 'en-US', 'USD'],
      ['auto', 'en-GB', 'GBP'],
      ['auto', 'de-DE', 'EUR'],
      [undefined, 'de-DE', 'EUR'],
    ])('resolves %s / %s to %s', (currency, regionFormat, expected) => {
      expect(resolveCurrency(currency, regionFormat)).toBe(expected)
    })
  })

  it('formats using the stored regional format, not the UI language', () => {
    const settings = useSettingsStore()
    settings.language = 'de'
    settings.regionFormat = 'de-CH'
    settings.currency = 'auto'

    const { regionFormat, currency, fmtNumber, fmtCurrency, fmtPercent } = useRegionalFormat()

    expect(regionFormat.value).toBe('de-CH')
    expect(currency.value).toBe('CHF')
    expect(fmtNumber(1234.5, { decimals: 2 })).toBe("1'234.50")
    expect(fmtCurrency(1234.5)).toBe(`CHF${NBSP}1'234.50`)
    expect(fmtPercent(12.34)).toBe(`12.3${NARROW_NBSP}%`)
  })

  it('derives the format from the language while set to auto', () => {
    const settings = useSettingsStore()
    settings.language = 'de'
    settings.regionFormat = 'auto'
    settings.currency = 'auto'

    const { regionFormat, currency, fmtNumber, fmtCurrency } = useRegionalFormat()

    expect(regionFormat.value).toBe('de-DE')
    expect(currency.value).toBe('EUR')
    expect(fmtNumber(1.05, { decimals: 3 })).toBe('1,050')
    expect(fmtCurrency(1234.5)).toBe(`1.234,50${NBSP}€`)
  })

  it('reacts to a regional format change without a language change', async () => {
    const settings = useSettingsStore()
    settings.language = 'de'
    settings.regionFormat = 'auto'

    const { fmtNumber } = useRegionalFormat()
    expect(fmtNumber(1.05, { decimals: 3 })).toBe('1,050')

    settings.regionFormat = 'en-GB'
    expect(fmtNumber(1.05, { decimals: 3 })).toBe('1.050')
    expect(settings.language).toBe('de')
  })
})
