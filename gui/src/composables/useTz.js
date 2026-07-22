import { useSettingsStore } from '@/stores/settings'

export function useTz() {
  const settings = useSettingsStore()
  const localeByLanguage = { de: 'de-CH', gsw: 'gsw-CH', en: 'en-GB', es: 'es-ES', fr: 'fr-CH', it: 'it-CH' }

  function formatPattern(date, pattern) {
    const locale = localeByLanguage[settings.language] ?? 'de-CH'
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: settings.timezone, weekday: 'long', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).reduce((values, part) => ({ ...values, [part.type]: part.value }), {})
    const named = new Intl.DateTimeFormat(locale, {
      timeZone: settings.timezone, weekday: 'long', month: 'long',
    }).formatToParts(date).reduce((values, part) => ({ ...values, [part.type]: part.value }), {})
    const shortNamed = new Intl.DateTimeFormat(locale, {
      timeZone: settings.timezone, weekday: 'short', month: 'short',
    }).formatToParts(date).reduce((values, part) => ({ ...values, [part.type]: part.value }), {})
    const replacements = {
      EEEE: named.weekday, EEE: shortNamed.weekday, EE: shortNamed.weekday,
      MMMM: named.month, MMM: shortNamed.month, MM: parts.month, M: String(Number(parts.month)),
      yyyy: parts.year, yy: parts.year.slice(-2), dd: parts.day, d: String(Number(parts.day)),
      HH: parts.hour, H: String(Number(parts.hour)), mm: parts.minute, m: String(Number(parts.minute)),
      ss: parts.second, s: String(Number(parts.second)),
    }
    const tokens = ['EEEE', 'MMMM', 'EEE', 'MMM', 'yyyy', 'EE', 'MM', 'yy', 'dd', 'HH', 'mm', 'ss', 'M', 'd', 'H', 'm', 's']
    return pattern.replace(/[A-Za-z]+/g, word => {
      const result = []
      let index = 0
      while (index < word.length) {
        const token = tokens.find(candidate => word.startsWith(candidate, index))
        if (token) {
          result.push(replacements[token])
          index += token.length
        } else if ((word[index] === 'T' || word[index] === 'h') && result.length) {
          result.push(word[index++])
        } else {
          return word
        }
      }
      return result.join('')
    })
  }

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
    if (!d || Number.isNaN(d.getTime())) return '—'
    return formatPattern(d, settings.dateFormat)
  }

  function fmtDateTime(iso) {
    const d = toUtcDate(iso)
    if (!d || Number.isNaN(d.getTime())) return '—'
    return `${formatPattern(d, settings.dateFormat)} ${formatPattern(d, settings.timeFormat)}`
  }

  function fmtChartLabel(iso) {
    const d = toUtcDate(iso)
    if (!d || Number.isNaN(d.getTime())) return ''
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
