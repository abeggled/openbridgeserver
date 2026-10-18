/**
 * OBS date/time token formatting for the Visu (issue #1073).
 *
 * Mirrors `gui/src/composables/useTz.js` so the Visu renders the administrator's
 * configured `date_format` / `time_format` patterns exactly like the Admin GUI
 * does — `dd.MM.yyyy`, `EEEE, MMMM d, yyyy`, `HH:mm:ss`, …
 *
 * The *pattern* comes from the server settings, the *names* from the UI
 * language: weekday and month names are translations and follow what the viewer
 * reads, while the regional format governs number and date conventions.
 */

export interface DateTimeNames {
  weekdays: string
  weekdaysShort: string
  weekdaysTwo: string
  months: string
  monthsShort: string
}

const WEEKDAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

// Longest first — `EEEE` must win over `EEE`, `yyyy` over `yy`, and so on.
const TOKENS = [
  'EEEE', 'MMMM', 'EEE', 'MMM', 'yyyy', 'EE', 'MM', 'yy', 'dd', 'HH', 'mm', 'ss', 'M', 'd', 'H', 'm', 's',
]

/**
 * Normalize a timestamp to a `Date`.
 *
 * - numbers / numeric strings → Unix milliseconds
 * - ISO strings without a zone → treated as UTC (SQLite aggregate buckets)
 * - ISO strings with a zone → parsed as-is
 */
export function toUtcDate(value: number | string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number' || /^\d+$/.test(String(value))) {
    const fromMs = new Date(Number(value))
    return Number.isNaN(fromMs.getTime()) ? null : fromMs
  }
  const text = String(value)
  const iso = /[Zz]$/.test(text) || /[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * Render *date* with the OBS token *pattern*, in *timeZone*, using *names* for
 * the localized weekday/month labels. Characters that are not tokens are kept.
 */
export function formatPattern(
  date: Date,
  pattern: string,
  timeZone: string | null,
  names: DateTimeNames,
): string {
  const options: Intl.DateTimeFormatOptions = {
    weekday: 'long',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }
  if (timeZone) options.timeZone = timeZone

  let parts: Record<string, string>
  try {
    parts = partsOf(date, options)
  } catch {
    // An unusable configured timezone must not break the whole widget.
    delete options.timeZone
    parts = partsOf(date, options)
  }

  const weekdayIndex = WEEKDAY_ORDER.indexOf(parts.weekday)
  const monthIndex = Number(parts.month) - 1
  const weekdays = names.weekdays.split('|')
  const weekdaysShort = names.weekdaysShort.split('|')
  const weekdaysTwo = names.weekdaysTwo.split('|')
  const months = names.months.split('|')
  const monthsShort = names.monthsShort.split('|')

  const replacements: Record<string, string> = {
    EEEE: weekdays[weekdayIndex],
    EEE: weekdaysShort[weekdayIndex],
    EE: weekdaysTwo[weekdayIndex],
    MMMM: months[monthIndex],
    MMM: monthsShort[monthIndex],
    MM: parts.month,
    M: String(Number(parts.month)),
    yyyy: parts.year,
    yy: parts.year.slice(-2),
    dd: parts.day,
    d: String(Number(parts.day)),
    HH: parts.hour,
    H: String(Number(parts.hour)),
    mm: parts.minute,
    m: String(Number(parts.minute)),
    ss: parts.second,
    s: String(Number(parts.second)),
  }

  return pattern.replace(/\p{L}+/gu, (word) => {
    const result: string[] = []
    let index = 0
    while (index < word.length) {
      const token = TOKENS.find((candidate) => word.startsWith(candidate, index))
      if (token) {
        result.push(replacements[token])
        index += token.length
      } else if ((word[index] === 'T' || word[index] === 'h') && result.length) {
        // Literal separators inside an otherwise tokenized word (e.g. `yyyy-MM-ddTHH`).
        result.push(word[index])
        index += 1
      } else {
        return word // not a pattern word at all — keep it verbatim
      }
    }
    return result.join('')
  })
}

function partsOf(date: Date, options: Intl.DateTimeFormatOptions): Record<string, string> {
  return new Intl.DateTimeFormat('en-CA', options)
    .formatToParts(date)
    .reduce<Record<string, string>>((values, part) => ({ ...values, [part.type]: part.value }), {})
}
