/**
 * Terminliste widget — turns iCalendar filter arrays into day groups (issue #999).
 *
 * The Logic block "iCalendar" emits one array per filter, each row being
 * `[date, start, end, summary, location, description]` with `date` as
 * `YYYY-MM-DD` and times as `HH:MM` (empty for all-day events), already in the
 * server timezone. Wired into a datapoint, that array reaches the Visu as is;
 * a JSON string of the same shape is accepted too.
 */

export interface CalendarEvent {
  date: string
  start: string
  end: string
  summary: string
  location: string
}

export interface CalendarSourceEvents {
  events: CalendarEvent[]
  color: string
  label: string
}

export interface DayEvent extends CalendarEvent {
  color: string
  sourceLabel: string
  sourceIndex: number
}

export type DayKind = 'today' | 'tomorrow' | 'date'

export interface DayGroup {
  date: string
  kind: DayKind
  events: DayEvent[]
}

export const MIN_DAYS = 1
export const MAX_DAYS = 366
export const DEFAULT_DAYS = 7

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** Parse a datapoint value into calendar events; anything unusable is dropped. */
export function parseEvents(value: unknown): CalendarEvent[] {
  let rows = value
  if (typeof rows === 'string') {
    try {
      rows = JSON.parse(rows)
    } catch {
      return []
    }
  }
  if (!Array.isArray(rows)) return []

  const events: CalendarEvent[] = []
  for (const row of rows) {
    if (!Array.isArray(row) || typeof row[0] !== 'string' || !ISO_DATE.test(row[0])) continue
    events.push({
      date: row[0],
      start: text(row[1]),
      end: text(row[2]),
      summary: text(row[3]),
      location: text(row[4]),
    })
  }
  return events
}

/** Clamp the configured number of days to a sane, whole range. */
export function clampDays(value: unknown): number {
  const days = Math.trunc(Number(value))
  if (!Number.isFinite(days)) return DEFAULT_DAYS
  return Math.min(MAX_DAYS, Math.max(MIN_DAYS, days))
}

/** Today's calendar date (`YYYY-MM-DD`) in *timeZone*, or in the browser zone. */
export function isoDateIn(now: Date, timeZone: string | null): string {
  const options: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit' }
  try {
    return new Intl.DateTimeFormat('en-CA', timeZone ? { ...options, timeZone } : options).format(now)
  } catch {
    // An unusable server timezone must not break the widget.
    return new Intl.DateTimeFormat('en-CA', options).format(now)
  }
}

/** Shift an ISO date by whole days, independent of any timezone. */
export function addDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10)
}

function compareEvents(a: DayEvent, b: DayEvent): number {
  // All-day events first, then by start time, then by source order.
  if (a.start !== b.start) {
    if (!a.start) return -1
    if (!b.start) return 1
    return a.start.localeCompare(b.start)
  }
  return a.sourceIndex - b.sourceIndex
}

/**
 * Merge all sources into one entry per day, from *today* for *days* days.
 * Days without events are left out unless *showEmptyDays* is set.
 */
export function buildDays(
  sources: CalendarSourceEvents[],
  today: string,
  days: number,
  showEmptyDays: boolean,
): DayGroup[] {
  const tomorrow = addDays(today, 1)
  const groups: DayGroup[] = []
  const byDate = new Map<string, DayGroup>()
  for (let offset = 0; offset < days; offset += 1) {
    const date = addDays(today, offset)
    const kind: DayKind = date === today ? 'today' : date === tomorrow ? 'tomorrow' : 'date'
    const group: DayGroup = { date, kind, events: [] }
    groups.push(group)
    byDate.set(date, group)
  }

  sources.forEach((source, sourceIndex) => {
    for (const event of source.events) {
      byDate.get(event.date)?.events.push({
        ...event,
        color: source.color,
        sourceLabel: source.label,
        sourceIndex,
      })
    }
  })

  for (const group of groups) group.events.sort(compareEvents)
  return showEmptyDays ? groups : groups.filter((group) => group.events.length > 0)
}
