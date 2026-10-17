import { describe, it, expect, vi } from 'vitest'
import {
  FALLBACK_REGION_FORMAT,
  formatCurrency,
  formatNumber,
  formatPercent,
  toFiniteNumber,
} from '@/utils/numberFormat'

const NBSP = '\u00A0'
const NARROW_NBSP = '\u202F'

describe('numberFormat (#1073)', () => {
  describe('toFiniteNumber', () => {
    it.each([
      [42, 42],
      [-0.5, -0.5],
      ['1.05', 1.05],
      ['  7 ', 7],
    ])('accepts %p as a number', (input, expected) => {
      expect(toFiniteNumber(input)).toBe(expected)
    })

    it.each([[null], [undefined], [''], ['   '], ['abc'], [true], [false], [NaN], [Infinity], [{}]])(
      'rejects %p',
      (input) => {
        expect(toFiniteNumber(input)).toBeNull()
      },
    )
  })

  describe('formatNumber', () => {
    it.each([
      ['de-DE', '1,050'],
      ['de-CH', '1.050'],
      ['en-US', '1.050'],
      ['fr-FR', '1,050'],
    ])('formats the issue example 1.05 with three decimals for %s', (locale, expected) => {
      expect(formatNumber(1.05, locale, { decimals: 3 })).toBe(expected)
    })

    it('groups thousands per regional format', () => {
      expect(formatNumber(1234567.5, 'de-DE', { decimals: 2 })).toBe('1.234.567,50')
      expect(formatNumber(1234567.5, 'de-CH', { decimals: 2 })).toBe("1'234'567.50")
      expect(formatNumber(1234567.5, 'en-US', { decimals: 2 })).toBe('1,234,567.50')
    })

    it('honours the locale minimum grouping digits (#1073)', () => {
      // CLDR minimumGroupingDigits = 1 → a separator from four digits on …
      expect(formatNumber(1234.5, 'de-DE')).toBe('1.234,5')
      expect(formatNumber(1234.5, 'en-US')).toBe('1,234.5')
      // … = 2 for Italian and Spanish → four digits stay ungrouped.
      expect(formatNumber(1234.5, 'it-IT')).toBe('1234,5')
      expect(formatNumber(1234.5, 'es-ES')).toBe('1234,5')
      expect(formatNumber(1234.5, 'it-CH')).toBe('1234.5')
      // Five digits group everywhere.
      expect(formatNumber(12345.5, 'it-IT')).toBe('12.345,5')
      expect(formatNumber(12345.5, 'es-ES')).toBe('12.345,5')
    })

    it('can suppress grouping', () => {
      expect(formatNumber(1234567.5, 'de-DE', { decimals: 2, grouping: false })).toBe('1234567,50')
    })

    it('keeps the value precision when no decimals are given', () => {
      expect(formatNumber(1.05, 'de-DE')).toBe('1,05')
      expect(formatNumber(42, 'de-DE')).toBe('42')
    })

    it('clamps out-of-range decimal counts', () => {
      expect(formatNumber(1.005, 'de-DE', { decimals: -3 })).toBe('1')
      expect(formatNumber(1.5, 'de-DE', { decimals: 99 })).toContain('1,5')
    })

    it('caps fraction digits without padding when maxDecimals is used', () => {
      expect(formatNumber(6.3333, 'de-DE', { maxDecimals: 2 })).toBe('6,33')
      expect(formatNumber(6, 'de-DE', { maxDecimals: 2 })).toBe('6')
      expect(formatNumber(6.5, 'de-DE', { maxDecimals: 0 })).toBe('7')
      expect(formatNumber(6.3333, 'de-DE', { maxDecimals: 99 })).toBe('6,3333')
    })

    it('lets decimals win over maxDecimals', () => {
      expect(formatNumber(6, 'de-DE', { decimals: 2, maxDecimals: 0 })).toBe('6,00')
    })

    it('never renders a nonzero value as zero (#1073)', () => {
      // Intl rounds anything below the fraction-digit ceiling away entirely.
      expect(formatNumber(1e-21, 'de-DE')).toBe('0,000000000000000000001')
      expect(formatNumber(1e-101, 'de-DE')).toBe('1e-101')
      expect(formatNumber(-1e-101, 'de-DE')).toBe('-1e-101')
      // A real zero still formats as zero.
      expect(formatNumber(0, 'de-DE')).toBe('0')
      // An explicit precision is the caller's decision and is honoured as given.
      expect(formatNumber(1e-21, 'de-DE', { decimals: 2 })).toBe('0,00')
    })

    it('falls back to the pre-ES2023 digit ceiling when the engine rejects 100', async () => {
      // Engines older than ES2023 throw above 20 fraction digits; the module
      // probes once at load time, so it has to be re-imported under the stub.
      const RealNumberFormat = Intl.NumberFormat
      class LimitedNumberFormat extends RealNumberFormat {
        constructor(locale, options = {}) {
          if ((options.maximumFractionDigits ?? 0) > 20) {
            throw new RangeError('maximumFractionDigits value is out of range.')
          }
          super(locale, options)
        }
      }
      vi.resetModules()
      vi.stubGlobal('Intl', { ...Intl, NumberFormat: LimitedNumberFormat })
      try {
        const limited = await import('@/utils/numberFormat')
        // No RangeError, and the tiny value still is not reported as zero.
        expect(limited.formatNumber(1.5, 'de-DE')).toBe('1,5')
        expect(limited.formatNumber(1e-21, 'de-DE')).toBe('1e-21')
      } finally {
        vi.unstubAllGlobals()
        vi.resetModules()
      }
    })

    it('tolerates a malformed persisted decimals value (#1073)', () => {
      // Widget config is an arbitrary persisted dictionary, so `decimals` can be
      // any junk from an import; Intl would otherwise throw into the render.
      expect(() => formatNumber(1.05, 'de-DE', { decimals: 'bad' })).not.toThrow()
      expect(formatNumber(1.05, 'de-DE', { decimals: 'bad' })).toBe('1')
      expect(formatNumber(1.05, 'de-DE', { decimals: null })).toBe('1,05')
      expect(formatNumber(1.05, 'de-DE', { maxDecimals: 'bad' })).toBe('1')
      expect(formatCurrency(1.05, 'de-DE', 'EUR', { decimals: 'bad' })).toContain('1')
    })

    it('returns non-numeric input unchanged', () => {
      expect(formatNumber('AN', 'de-DE')).toBe('AN')
      expect(formatNumber(null, 'de-DE')).toBe('')
      expect(formatNumber(undefined, 'de-DE')).toBe('')
      expect(formatNumber(true, 'de-DE')).toBe('true')
    })

    it('falls back to the default regional format for an invalid locale', () => {
      expect(formatNumber(1234.5, 'not a locale', { decimals: 1 })).toBe(
        formatNumber(1234.5, FALLBACK_REGION_FORMAT, { decimals: 1 }),
      )
    })

    it('uses the default locale when none is given', () => {
      expect(formatNumber(1.5)).toBe('1,5')
    })
  })

  describe('formatCurrency', () => {
    it('renders the configured currency for the regional format', () => {
      expect(formatCurrency(1234.5, 'de-DE', 'EUR')).toBe(`1.234,50${NBSP}€`)
      expect(formatCurrency(1234.5, 'de-CH', 'CHF')).toBe(`CHF${NBSP}1'234.50`)
    })

    it('honours a decimals override and non-numeric input', () => {
      expect(formatCurrency(1234.5, 'de-DE', 'EUR', { decimals: 0 })).toBe(`1.235${NBSP}€`)
      expect(formatCurrency('n/a', 'de-DE', 'EUR')).toBe('n/a')
      expect(formatCurrency(null, 'de-DE', 'EUR')).toBe('')
    })

    it('degrades to a plain number when the currency code is unusable', () => {
      // An invalid currency can reach the store through a hand-edited backup;
      // it must not throw into a component render (#1073).
      expect(formatCurrency(1234.5, 'de-DE', 'x')).toContain('1.234,5')
      expect(formatCurrency(1234.5, 'not a locale', 'x')).toContain('1.234,5')
    })

    it('uses the defaults when locale and currency are omitted', () => {
      expect(formatCurrency(1)).toBe(`1,00${NBSP}€`)
    })
  })

  describe('formatPercent', () => {
    it('appends a percent sign to the localized number', () => {
      expect(formatPercent(42.55, 'de-DE')).toBe(`42,6${NARROW_NBSP}%`)
      expect(formatPercent(42.55, 'en-US', { decimals: 2 })).toBe(`42.55${NARROW_NBSP}%`)
    })

    it('returns non-numeric input unchanged', () => {
      expect(formatPercent('—', 'de-DE')).toBe('—')
      expect(formatPercent(undefined, 'de-DE')).toBe('')
    })

    it('uses the default locale when none is given', () => {
      expect(formatPercent(1)).toBe(`1,0${NARROW_NBSP}%`)
    })
  })
})
