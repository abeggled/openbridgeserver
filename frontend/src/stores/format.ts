/**
 * Pinia-Store: Regionalformat für die Visu (Issue #1073)
 *
 * Das Regionalformat ist eine eigene Server-Einstellung und bewusst *unabhängig*
 * von der UI-Sprache: Deutsch in der Schweiz formatiert `1'234.50`, Deutsch in
 * Deutschland `1.234,50`. Der Store lädt die öffentliche Display-Settings-Route
 * (auch ohne Login erreichbar, weil die Visu anonym/per PIN genutzt wird) und
 * stellt daraus abgeleitete Formatierer bereit.
 *
 * Aufteilung zwischen Server- und Betrachter-Einstellung:
 *
 * - **Formatkonventionen** (Trennzeichen, Datums-/Zeitmuster, Währung) kommen aus
 *   den Server-Einstellungen und sind für *alle* Betrachter identisch. Auch
 *   `auto` löst gegen die *konfigurierte* Sprache auf, nicht gegen die im
 *   Browser erkannte: eine Anlage hat eine Zahlenkonvention, sonst sähen zwei
 *   Betrachter derselben Visu-Seite unterschiedliche Werte und die Visu würde
 *   von der Admin-GUI abweichen.
 * - **Namen** (Wochentage, Monate) sind Übersetzungen und folgen der UI-Sprache
 *   des Betrachters, die `App.vue` über `setUiLanguage()` nachführt.
 *
 * Nur Darstellung — Datenpunktwerte, Konfiguration, Berechnungen, API-Payloads
 * und History bleiben locale-neutrale Zahlen.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { displaySettings as displaySettingsApi } from '@/api/client'
import deNames from '@/locales/de.json'
import enNames from '@/locales/en.json'
import esNames from '@/locales/es.json'
import frNames from '@/locales/fr.json'
import gswNames from '@/locales/gsw.json'
import itNames from '@/locales/it.json'
import { formatPattern, toUtcDate, type DateTimeNames } from '@/utils/datePattern'
import {
  FALLBACK_REGION_FORMAT,
  formatCurrency,
  formatNumber,
  formatPercent,
  resolveCurrency,
  resolveRegionFormat,
  type NumberFormatOptions,
} from '@/utils/numberFormat'

export const DEFAULT_DATE_FORMAT = 'dd.MM.yyyy'
export const DEFAULT_TIME_FORMAT = 'HH:mm:ss'

/**
 * Weekday/month names per UI language — one entry for every locale in
 * `SUPPORTED_LOCALES`, mirroring `gui/src/composables/useTz.js`. They are
 * translations, so they follow what the viewer reads rather than the regional
 * format; an unknown language falls back to English, like vue-i18n does.
 */
function namesOf(bundle: unknown): DateTimeNames {
  return (bundle as { datetimeNames: DateTimeNames }).datetimeNames
}

const LOCALE_NAMES: Record<string, DateTimeNames> = {
  de: namesOf(deNames),
  en: namesOf(enNames),
  es: namesOf(esNames),
  fr: namesOf(frNames),
  gsw: namesOf(gswNames),
  it: namesOf(itNames),
}

export const useFormatStore = defineStore('format', () => {
  const language = ref('de')
  // Holds the server-resolved format when the endpoint supplies one, otherwise
  // the raw setting (possibly `auto`) — `regionFormat` below resolves either.
  const regionFormatSetting = ref('auto')
  const currencySetting = ref('auto')
  const timezone = ref<string | null>(null)
  // UI language for weekday/month names. Kept in sync by App.vue rather than
  // read from the i18n singleton, so the store stays independent of it.
  const uiLanguage = ref('de')
  // Administrator-configured display patterns, shared with the Admin GUI.
  const dateFormat = ref(DEFAULT_DATE_FORMAT)
  const timeFormat = ref(DEFAULT_TIME_FORMAT)
  const loaded = ref(false)

  const regionFormat = computed(() => resolveRegionFormat(regionFormatSetting.value, language.value))
  const currency = computed(() => resolveCurrency(currencySetting.value, regionFormat.value))

  /** Load once at app start; failures keep the German defaults. */
  async function load(): Promise<void> {
    try {
      const data = await displaySettingsApi.get()
      language.value = data.language || language.value
      regionFormatSetting.value = data.resolved_region_format || data.region_format || regionFormatSetting.value
      currencySetting.value = data.resolved_currency || data.currency || currencySetting.value
      timezone.value = data.timezone || null
      dateFormat.value = data.date_format || dateFormat.value
      timeFormat.value = data.time_format || timeFormat.value
    } catch {
      // Visu stays usable with the fallback format when the route is unreachable.
    }
    loaded.value = true
  }

  function fmtNumber(value: unknown, options?: NumberFormatOptions): string {
    return formatNumber(value, regionFormat.value, options)
  }

  function fmtCurrency(value: unknown, options?: { decimals?: number }): string {
    return formatCurrency(value, regionFormat.value, currency.value, options)
  }

  function fmtPercent(value: unknown, options?: { decimals?: number }): string {
    return formatPercent(value, regionFormat.value, options)
  }

  /** Track the active UI language; names are translations and follow it. */
  function setUiLanguage(code: string): void {
    uiLanguage.value = code
  }

  /** Weekday/month names for the active UI language, English as fallback. */
  function localeNames(): DateTimeNames {
    return LOCALE_NAMES[uiLanguage.value] ?? LOCALE_NAMES.en
  }

  /**
   * Date only, in the administrator-configured `date_format` pattern.
   *
   * *timeZone* overrides the server timezone for widgets that carry their own
   * (the clock widget renders a freely selectable zone).
   */
  function fmtDate(value: number | string | Date, timeZone?: string | null): string {
    const date = toUtcDate(value)
    if (!date) return ''
    return formatPattern(date, dateFormat.value, timeZone || timezone.value, localeNames())
  }

  /** Time only, in the administrator-configured `time_format` pattern. */
  function fmtTime(value: number | string | Date, timeZone?: string | null): string {
    const date = toUtcDate(value)
    if (!date) return ''
    return formatPattern(date, timeFormat.value, timeZone || timezone.value, localeNames())
  }

  /**
   * Date/time text for timestamps, chart axes and tooltips.
   *
   * Without *options* the administrator-configured `date_format` and
   * `time_format` patterns are applied, exactly like the Admin GUI does. Pass
   * explicit `Intl` component options for compact renderings such as chart axis
   * labels, where the full configured pattern would not fit.
   */
  function fmtDateTime(value: number | string | Date, options?: Intl.DateTimeFormatOptions): string {
    if (!options) {
      const date = toUtcDate(value)
      if (!date) return ''
      const names = localeNames()
      return `${formatPattern(date, dateFormat.value, timezone.value, names)} `
        + `${formatPattern(date, timeFormat.value, timezone.value, names)}`
    }
    const date = value instanceof Date ? value : new Date(value)
    if (Number.isNaN(date.getTime())) return ''
    // Server timezone as the default; an explicit caller option still wins.
    const intlOptions: Intl.DateTimeFormatOptions = timezone.value
      ? { timeZone: timezone.value, ...options }
      : { ...options }
    try {
      return new Intl.DateTimeFormat(regionFormat.value, intlOptions).format(date)
    } catch {
      // Unusable regional format — keep the configured timezone …
    }
    try {
      return new Intl.DateTimeFormat(FALLBACK_REGION_FORMAT, intlOptions).format(date)
    } catch {
      // … and only drop it when the timezone itself is the unusable part.
      return new Intl.DateTimeFormat(FALLBACK_REGION_FORMAT, options).format(date)
    }
  }

  return {
    language,
    regionFormatSetting,
    currencySetting,
    timezone,
    uiLanguage,
    dateFormat,
    timeFormat,
    loaded,
    regionFormat,
    currency,
    load,
    setUiLanguage,
    fmtNumber,
    fmtCurrency,
    fmtPercent,
    fmtDate,
    fmtTime,
    fmtDateTime,
  }
})
