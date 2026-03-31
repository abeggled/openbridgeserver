import { useSettingsStore } from '@/stores/settings'

export function useTz() {
  const settings = useSettingsStore()

  function fmtDate(iso) {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('de-CH', {
      timeZone: settings.timezone,
      year: 'numeric', month: '2-digit', day: '2-digit',
    })
  }

  function fmtDateTime(iso) {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('de-CH', {
      timeZone: settings.timezone,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  }

  function fmtChartLabel(iso) {
    if (!iso) return ''
    return new Date(iso).toLocaleString('de-CH', {
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

  return { fmtDate, fmtDateTime, fmtChartLabel, toDatetimeLocal, fromDatetimeLocal }
}
