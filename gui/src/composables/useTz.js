import { useSettingsStore } from '@/stores/settings'

export function useTz() {
  const settings = useSettingsStore()

  // Normalize a timestamp to a valid UTC Date:
  //   - numbers / numeric strings  → treat as Unix ms
  //   - ISO strings without tz     → append "Z" to force UTC (SQLite aggregate buckets)
  //   - ISO strings with tz        → parse as-is
  function toUtcDate(iso) {
    if (iso == null || iso === '') return null
    if (typeof iso === 'number' || (typeof iso === 'string' && /^\d+$/.test(iso))) {
      return new Date(Number(iso))
    }
    const s = String(iso)
    if (/[Zz]$/.test(s) || /[+-]\d{2}:\d{2}$/.test(s)) return new Date(s)
    return new Date(s + 'Z')
  }

  function fmtDate(iso) {
    const d = toUtcDate(iso)
    if (!d) return '—'
    return d.toLocaleDateString('de-CH', {
      timeZone: settings.timezone,
      year: 'numeric', month: '2-digit', day: '2-digit',
    })
  }

  function fmtDateTime(iso) {
    const d = toUtcDate(iso)
    if (!d) return '—'
    return d.toLocaleString('de-CH', {
      timeZone: settings.timezone,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  }

  function fmtChartLabel(iso) {
    const d = toUtcDate(iso)
    if (!d) return ''
    return d.toLocaleString('de-CH', {
      timeZone: settings.timezone,
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  }

  function toDatetimeLocal(date) {
    // Returns 'YYYY-MM-DDTHH:MM' formatted for datetime-local inputs
    const d = date instanceof Date ? date : new Date(date)
    const pad = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  }

  function fromDatetimeLocal(str) {
    // Converts datetime-local string back to ISO string
    if (!str) return null
    return new Date(str).toISOString()
  }

  return { fmtDate, fmtDateTime, fmtChartLabel, toDatetimeLocal, fromDatetimeLocal, toUtcDate }
}
