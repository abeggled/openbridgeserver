import { useSettingsStore } from '@/stores/settings'

export function useTz() {
  const settings = useSettingsStore()
  const names = {
    de: {
      weekdays: 'Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag',
      weekdaysShort: 'Mo.|Di.|Mi.|Do.|Fr.|Sa.|So.', weekdaysTwo: 'Mo|Di|Mi|Do|Fr|Sa|So',
      months: 'Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember',
      monthsShort: 'Jan.|Feb.|März|Apr.|Mai|Juni|Juli|Aug.|Sept.|Okt.|Nov.|Dez.',
    },
    gsw: {
      weekdays: 'Mäntig|Zischtig|Mittwuch|Dunschtig|Friitig|Samschtig|Sunntig',
      weekdaysShort: 'Mä.|Zi.|Mi.|Du.|Fr.|Sa.|Su.', weekdaysTwo: 'Mä|Zi|Mi|Du|Fr|Sa|Su',
      months: 'Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember',
      monthsShort: 'Jan.|Feb.|März|Apr.|Mai|Juni|Juli|Aug.|Sept.|Okt.|Nov.|Dez.',
    },
    en: {
      weekdays: 'Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday',
      weekdaysShort: 'Mon|Tue|Wed|Thu|Fri|Sat|Sun', weekdaysTwo: 'Mo|Tu|We|Th|Fr|Sa|Su',
      months: 'January|February|March|April|May|June|July|August|September|October|November|December',
      monthsShort: 'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec',
    },
    es: {
      weekdays: 'lunes|martes|miércoles|jueves|viernes|sábado|domingo',
      weekdaysShort: 'lun|mar|mié|jue|vie|sáb|dom', weekdaysTwo: 'lu|ma|mi|ju|vi|sá|do',
      months: 'enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre',
      monthsShort: 'ene|feb|mar|abr|may|jun|jul|ago|sept|oct|nov|dic',
    },
    fr: {
      weekdays: 'lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche',
      weekdaysShort: 'lun.|mar.|mer.|jeu.|ven.|sam.|dim.', weekdaysTwo: 'lu|ma|me|je|ve|sa|di',
      months: 'janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre',
      monthsShort: 'janv.|févr.|mars|avr.|mai|juin|juil.|août|sept.|oct.|nov.|déc.',
    },
    it: {
      weekdays: 'lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica',
      weekdaysShort: 'lun|mar|mer|gio|ven|sab|dom', weekdaysTwo: 'lu|ma|me|gi|ve|sa|do',
      months: 'gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre',
      monthsShort: 'gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic',
    },
  }

  function formatPattern(date, pattern) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: settings.timezone, weekday: 'long', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).reduce((values, part) => ({ ...values, [part.type]: part.value }), {})
    const selectedNames = names[settings.language] ?? names.en
    const weekdayIndex = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].indexOf(parts.weekday)
    const monthIndex = Number(parts.month) - 1
    const weekdays = selectedNames.weekdays.split('|')
    const weekdaysShort = selectedNames.weekdaysShort.split('|')
    const weekdaysTwo = selectedNames.weekdaysTwo.split('|')
    const months = selectedNames.months.split('|')
    const monthsShort = selectedNames.monthsShort.split('|')
    const replacements = {
      EEEE: weekdays[weekdayIndex], EEE: weekdaysShort[weekdayIndex], EE: weekdaysTwo[weekdayIndex],
      MMMM: months[monthIndex], MMM: monthsShort[monthIndex], MM: parts.month, M: String(Number(parts.month)),
      yyyy: parts.year, yy: parts.year.slice(-2), dd: parts.day, d: String(Number(parts.day)),
      HH: parts.hour, H: String(Number(parts.hour)), mm: parts.minute, m: String(Number(parts.minute)),
      ss: parts.second, s: String(Number(parts.second)),
    }
    const tokens = ['EEEE', 'MMMM', 'EEE', 'MMM', 'yyyy', 'EE', 'MM', 'yy', 'dd', 'HH', 'mm', 'ss', 'M', 'd', 'H', 'm', 's']
    return pattern.replace(/\p{L}+/gu, word => {
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
