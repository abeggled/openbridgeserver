/**
 * useTz — timezone-aware date/time formatting composable.
 *
 * Uses the configured application timezone (from settings store) with
 * Intl.DateTimeFormat so that all timestamp displays respect the same
 * timezone — independent of the user's browser locale.
 */
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settings'

export function useTz() {
  const settings = useSettingsStore()

  /** Configured IANA timezone string, e.g. "Europe/Zurich" */
  const timezone = computed(() => settings.timezone)

  /**
   * Format a UTC timestamp string (ISO 8601) as a locale date+time string
   * in the configured timezone.
   * @param {string|Date} ts
   * @returns {string}
   */
  function fmtDateTime(ts) {
    if (!ts) return '—'
    try {
      return new Intl.DateTimeFormat('de-CH', {
        timeZone:  settings.timezone,
        year:      'numeric',
        month:     '2-digit',
        day:       '2-digit',
        hour:      '2-digit',
        minute:    '2-digit',
        second:    '2-digit',
      }).format(new Date(ts))
    } catch {
      return new Date(ts).toLocaleString('de-CH')
    }
  }

  /**
   * Format a UTC timestamp as a date-only string in the configured timezone.
   * @param {string|Date} ts
   * @returns {string}
   */
  function fmtDate(ts) {
    if (!ts) return '—'
    try {
      return new Intl.DateTimeFormat('de-CH', {
        timeZone: settings.timezone,
        year:     'numeric',
        month:    '2-digit',
        day:      '2-digit',
      }).format(new Date(ts))
    } catch {
      return new Date(ts).toLocaleDateString('de-CH')
    }
  }

  /**
   * Format a UTC timestamp for a chart axis label (compact).
   * @param {string|Date} ts
   * @returns {string}
   */
  function fmtChartLabel(ts) {
    if (!ts) return ''
    try {
      return new Intl.DateTimeFormat('de-CH', {
        timeZone: settings.timezone,
        month:    'short',
        day:      '2-digit',
        hour:     '2-digit',
        minute:   '2-digit',
      }).format(new Date(ts))
    } catch {
      return new Date(ts).toLocaleString('de-CH', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    }
  }

  return { timezone, fmtDateTime, fmtDate, fmtChartLabel }
}
