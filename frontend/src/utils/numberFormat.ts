/**
 * Locale-aware number/currency display formatting for the Visu (issue #1073).
 *
 * The formatting locale is the *regional format* setting, which is deliberately
 * independent of the UI language: a German UI in Switzerland shows `1'234.50`,
 * the same German UI in Germany shows `1.234,50`.
 *
 * These helpers are display-only. Datapoint values, widget configuration,
 * calculations, API payloads and stored history keep locale-neutral numbers.
 */

export const FALLBACK_REGION_FORMAT = 'de-DE'

/** Typographic separator between number and percent sign. */
const NARROW_NBSP = '\u202F'

export interface NumberFormatOptions {
  /** Exact fraction digits, padded with zeros. */
  decimals?: number | null
  /** Upper bound on fraction digits (trailing zeros dropped); ignored when `decimals` is set. */
  maxDecimals?: number | null
  grouping?: boolean
}

/**
 * `useGrouping` accepts "auto" | "always" | "min2" since ES2023, but the
 * TypeScript lib in use still types it as a boolean.
 */
type IntlNumberOptions = Omit<Intl.NumberFormatOptions, 'useGrouping'> & {
  useGrouping?: boolean | 'auto' | 'always' | 'min2'
}

/**
 * ES2023 raised the `maximumFractionDigits` ceiling from 20 to 100; older
 * engines throw a RangeError above 20, so probe once and use what is supported.
 */
const MAX_FRACTION_DIGITS = (() => {
  try {
    new Intl.NumberFormat('en', { maximumFractionDigits: 100 })
    return 100
  } catch {
    return 20
  }
})()

/** True when the rendered text carries no significant digit at all. */
function collapsedToZero(text: string): boolean {
  return !/[1-9]/.test(text)
}

const formatterCache = new Map<string, Intl.NumberFormat>()

function getFormatter(locale: string, options: IntlNumberOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`
  let formatter = formatterCache.get(key)
  if (!formatter) {
    formatter = buildFormatter(locale, options)
    formatterCache.set(key, formatter)
  }
  return formatter
}

/**
 * Both the locale and the options can be rejected by `Intl` — an unsupported
 * regional format, or a currency code that reached the database through an
 * unvalidated config import. Degrade step by step instead of throwing into a
 * component render.
 */
function buildFormatter(locale: string, options: IntlNumberOptions): Intl.NumberFormat {
  try {
    return new Intl.NumberFormat(locale, options as Intl.NumberFormatOptions)
  } catch {
    // Unusable locale — retry with the default, keeping the requested options.
  }
  try {
    return new Intl.NumberFormat(FALLBACK_REGION_FORMAT, options as Intl.NumberFormatOptions)
  } catch {
    // Unusable options (e.g. an invalid currency code): drop to a plain number
    // so the amount stays readable.
    const { style: _style, currency: _currency, ...rest } = options
    return new Intl.NumberFormat(FALLBACK_REGION_FORMAT, rest as Intl.NumberFormatOptions)
  }
}

/**
 * Coerce a value to a finite number, or return `null` when it is not numeric.
 * Booleans are not numbers here — they are rendered as labels, not values.
 */
export function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function clampDigits(digits: number): number {
  // A widget config is an arbitrary persisted dictionary, so `decimals` can be
  // a non-numeric value from an import or API write. Math.trunc() would yield
  // NaN and Intl would then throw straight into the render (issue #1073).
  const truncated = Math.trunc(Number(digits))
  if (!Number.isFinite(truncated)) return 0
  return Math.max(0, Math.min(MAX_FRACTION_DIGITS, truncated))
}

function passthrough(value: unknown): string {
  return value === null || value === undefined ? '' : String(value)
}

/**
 * Format a number for display.
 *
 * `decimals` fixes the fraction digits, `maxDecimals` only caps them; with
 * neither, the value's own precision is kept. Non-numeric input is returned
 * unchanged.
 */
export function formatNumber(
  value: unknown,
  locale: string = FALLBACK_REGION_FORMAT,
  options: NumberFormatOptions = {},
): string {
  const { decimals = null, maxDecimals = null, grouping = true } = options
  const number = toFiniteNumber(value)
  if (number === null) return passthrough(value)
  // `true` normalises to "always", which overrides the locale's CLDR
  // minimumGroupingDigits; "auto" honours it (es/it group only from five
  // digits). Pre-ES2023 engines coerce the string to true — today's behaviour.
  const intlOptions: IntlNumberOptions = { useGrouping: grouping ? 'auto' : false }
  if (decimals !== null && decimals !== undefined) {
    const digits = clampDigits(decimals)
    intlOptions.minimumFractionDigits = digits
    intlOptions.maximumFractionDigits = digits
  } else if (maxDecimals !== null && maxDecimals !== undefined) {
    intlOptions.maximumFractionDigits = clampDigits(maxDecimals)
  } else {
    intlOptions.maximumFractionDigits = MAX_FRACTION_DIGITS
  }
  const text = getFormatter(locale, intlOptions).format(number)
  // Never report a nonzero measurement as 0 — below the digit ceiling Intl
  // rounds tiny values away entirely (issue #1073). Truth beats formatting.
  if (number !== 0 && decimals === null && collapsedToZero(text)) return String(number)
  return text
}

/** Format a monetary amount with the configured currency. */
export function formatCurrency(
  value: unknown,
  locale: string = FALLBACK_REGION_FORMAT,
  currency = 'EUR',
  options: { decimals?: number } = {},
): string {
  const { decimals = 2 } = options
  const number = toFiniteNumber(value)
  if (number === null) return passthrough(value)
  const digits = clampDigits(decimals)
  return getFormatter(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(number)
}

/** Format a ratio already expressed in percent (e.g. `42.5` → `42,5 %`). */
export function formatPercent(
  value: unknown,
  locale: string = FALLBACK_REGION_FORMAT,
  options: { decimals?: number } = {},
): string {
  const { decimals = 1 } = options
  const number = toFiniteNumber(value)
  if (number === null) return passthrough(value)
  return `${formatNumber(number, locale, { decimals })}${NARROW_NBSP}%`
}

const LANGUAGE_REGION: Record<string, string> = {
  de: 'de-DE',
  gsw: 'de-CH',
  en: 'en-US',
  fr: 'fr-FR',
  it: 'it-IT',
  es: 'es-ES',
}

const REGION_CURRENCY: Record<string, string> = {
  'de-CH': 'CHF',
  'fr-CH': 'CHF',
  'it-CH': 'CHF',
  'en-US': 'USD',
  'en-GB': 'GBP',
}

/** Resolve `auto` against the UI language; anything else is used verbatim. */
export function resolveRegionFormat(regionFormat?: string | null, language?: string | null): string {
  if (regionFormat && regionFormat !== 'auto') return regionFormat
  return LANGUAGE_REGION[language ?? ''] ?? FALLBACK_REGION_FORMAT
}

/** Resolve `auto` against the effective regional format. */
export function resolveCurrency(currency?: string | null, regionFormat?: string | null): string {
  if (currency && currency !== 'auto') return currency
  return REGION_CURRENCY[regionFormat ?? ''] ?? 'EUR'
}
