import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { formatCurrency, formatNumber, formatPercent } from '@/utils/numberFormat'

/**
 * Regional display formatting bound to the configured application settings.
 *
 * The regional format is its own setting and is *not* derived from the UI
 * language — only `auto` falls back to a language-based default (issue #1073).
 * Everything here is display-only; stored values and payloads stay neutral.
 */

const LANGUAGE_REGION = {
  de: 'de-DE',
  gsw: 'de-CH',
  en: 'en-US',
  fr: 'fr-FR',
  it: 'it-IT',
  es: 'es-ES',
}

const REGION_CURRENCY = {
  'de-CH': 'CHF',
  'fr-CH': 'CHF',
  'it-CH': 'CHF',
  'en-US': 'USD',
  'en-GB': 'GBP',
}

/** Resolve `auto` against the UI language; anything else is used verbatim. */
export function resolveRegionFormat(regionFormat, language) {
  if (regionFormat && regionFormat !== 'auto') return regionFormat
  return LANGUAGE_REGION[language] ?? 'de-DE'
}

/** Resolve `auto` against the effective regional format. */
export function resolveCurrency(currency, regionFormat) {
  if (currency && currency !== 'auto') return currency
  return REGION_CURRENCY[regionFormat] ?? 'EUR'
}

export function useRegionalFormat() {
  const settings = useSettingsStore()

  const regionFormat = computed(() => resolveRegionFormat(settings.regionFormat, settings.language))
  const currency = computed(() => resolveCurrency(settings.currency, regionFormat.value))

  return {
    regionFormat,
    currency,
    fmtNumber: (value, options) => formatNumber(value, regionFormat.value, options),
    fmtCurrency: (value, options) => formatCurrency(value, regionFormat.value, currency.value, options),
    fmtPercent: (value, options) => formatPercent(value, regionFormat.value, options),
  }
}
