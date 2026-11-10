/**
 * Calendar-aligned ticks for the chart time axis (issue #1430).
 *
 * Chart.js' linear scale picks "nice" numbers in milliseconds, which are not
 * nice in time: 7 days / 5 becomes 2·10⁸ ms = 55 h 33 min, and the grid lines
 * land on arbitrary clock times. Instead, the step comes from a fixed ladder of
 * time units sized to the chart width, and every tick sits on a wall-clock
 * boundary (full minutes/hours, midnight, Monday, first of the month) in the
 * configured server timezone — including across DST changes.
 */

import type { DateTimeNames } from '@/utils/datePattern'

export type TimeTickUnit = 'minute' | 'hour' | 'day' | 'week' | 'month'

export interface TimeTickStep {
  unit: TimeTickUnit
  count: number
  /** Nominal step length, used only to size the step to the range. */
  approxMs: number
}

/**
 * - `time`: intraday tick, labelled with the clock time
 * - `day`: tick at local midnight, labelled with weekday and date
 * - `month`: tick on the first of a month, labelled with month and year
 */
export type CalendarTickKind = 'time' | 'day' | 'month'

export interface CalendarTick {
  value: number
  /** On the boundary of the next larger unit (midnight, month start, new year). */
  major: boolean
  kind: CalendarTickKind
  /** Local calendar fields of the tick; weekday 0 = Monday. */
  year: number
  month: number
  weekday: number
}

const MINUTE_MS = 60_000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS
const MINUTES_PER_DAY = 24 * 60

/** Every minute and hour step divides a day, so ticks repeat on each local day. */
export const TIME_TICK_STEPS: TimeTickStep[] = [
  ...[1, 2, 5, 10, 15, 30].map(count => ({ unit: 'minute' as const, count, approxMs: count * MINUTE_MS })),
  ...[1, 2, 3, 6, 12].map(count => ({ unit: 'hour' as const, count, approxMs: count * HOUR_MS })),
  ...[1, 2].map(count => ({ unit: 'day' as const, count, approxMs: count * DAY_MS })),
  { unit: 'week', count: 1, approxMs: 7 * DAY_MS },
  ...[1, 3, 6, 12].map(count => ({ unit: 'month' as const, count, approxMs: count * 30 * DAY_MS })),
]

/** Minimum horizontal room per label, enough for `Mo 02.11.` or `06:00 PM`. */
export const MIN_TICK_SPACING_PX = 80

export function maxTicksForWidth(widthPx: number, spacingPx = MIN_TICK_SPACING_PX): number {
  if (!Number.isFinite(widthPx) || widthPx <= 0) return 2
  return Math.max(2, Math.floor(widthPx / spacingPx))
}

/** Smallest step that keeps the range within *maxTicks* ticks. */
export function chooseTimeTickStep(rangeMs: number, maxTicks: number): TimeTickStep {
  const limit = Math.max(1, maxTicks)
  return TIME_TICK_STEPS.find(step => rangeMs / step.approxMs <= limit)
    ?? TIME_TICK_STEPS[TIME_TICK_STEPS.length - 1]
}

interface ZonedParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

const formatterCache = new Map<string, Intl.DateTimeFormat>()

function formatterFor(timeZone: string | null): Intl.DateTimeFormat {
  const key = timeZone ?? ''
  const cached = formatterCache.get(key)
  if (cached) return cached
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric',
    hourCycle: 'h23',
  }
  let formatter: Intl.DateTimeFormat
  try {
    formatter = new Intl.DateTimeFormat('en-US', timeZone ? { ...options, timeZone } : options)
  } catch {
    // An unusable configured timezone falls back to the browser zone, like the formatters do.
    formatter = new Intl.DateTimeFormat('en-US', options)
  }
  formatterCache.set(key, formatter)
  return formatter
}

/** Wall-clock fields of *ms* in *timeZone* (`null` = browser zone). */
export function zonedParts(ms: number, timeZone: string | null): ZonedParts {
  const fields: Record<string, number> = {}
  for (const part of formatterFor(timeZone).formatToParts(new Date(ms))) {
    if (part.type !== 'literal') fields[part.type] = Number(part.value)
  }
  return {
    year: fields.year,
    month: fields.month,
    day: fields.day,
    // Some engines render midnight as 24 even with h23.
    hour: fields.hour % 24,
    minute: fields.minute,
    second: fields.second,
  }
}

/** UTC offset of *timeZone* at *ms*, in milliseconds. */
function offsetAt(ms: number, timeZone: string | null): number {
  const p = zonedParts(ms, timeZone)
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
  return wall - Math.floor(ms / 1000) * 1000
}

/**
 * Every instant of the local wall-clock time (*year*, *month*, *day*,
 * *minuteOfDay*) in *timeZone*, in ascending order:
 * - usually one instant
 * - two for a time repeated by a fall-back change (02:15 on the autumn day)
 * - for a time skipped by a spring-forward change, the instant right after the
 *   gap (02:30 becomes 03:30)
 */
export function zonedToUtcAll(
  year: number,
  month: number,
  day: number,
  minuteOfDay: number,
  timeZone: string | null,
): number[] {
  const wall = Date.UTC(year, month - 1, day, 0, minuteOfDay)
  // UTC offsets never exceed ±14 h, so the offsets a day before and after
  // cover both sides of any DST change that can affect this wall time.
  const offsets = [...new Set([-DAY_MS, 0, DAY_MS].map(delta => offsetAt(wall + delta, timeZone)))]
  const instants = offsets
    .map(offset => wall - offset)
    .filter(ms => wall - offsetAt(ms, timeZone) === ms)
  if (instants.length === 0) return [wall - Math.min(...offsets)]
  return [...new Set(instants)].sort((a, b) => a - b)
}

/**
 * First instant of a local wall-clock time in *timeZone*; see `zonedToUtcAll`
 * for repeated and skipped times.
 */
export function zonedToUtc(
  year: number,
  month: number,
  day: number,
  minuteOfDay: number,
  timeZone: string | null,
): number {
  return zonedToUtcAll(year, month, day, minuteOfDay, timeZone)[0]
}

/** Calendar date arithmetic independent of any timezone. */
function addDays(year: number, month: number, day: number, days: number): [number, number, number] {
  const d = new Date(Date.UTC(year, month - 1, day + days))
  return [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()]
}

function epochDay(year: number, month: number, day: number): number {
  return Math.round(Date.UTC(year, month - 1, day) / DAY_MS)
}

function weekdayOf(year: number, month: number, day: number): number {
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7
}

// Upper bound on candidates, so a degenerate range can never spin.
const MAX_CANDIDATES = 5000
const LEAD_MS = 3 * HOUR_MS

/**
 * Ticks for [*fromMs*, *toMs*] on the wall-clock boundaries of *step* in
 * *timeZone*. The range bounds are not added as ticks themselves.
 */
export function calendarTicks(
  fromMs: number,
  toMs: number,
  step: TimeTickStep,
  timeZone: string | null,
): CalendarTick[] {
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs < fromMs) return []

  const ticks: CalendarTick[] = []
  const push = (ms: number, year: number, month: number, day: number, kind: CalendarTickKind, major: boolean) => {
    // A skipped DST hour can resolve onto the next candidate — keep ticks strictly increasing.
    // Candidates past the end never get here: the loops stop or filter before.
    if (ms < fromMs) return
    if (ticks.length && ms <= ticks[ticks.length - 1].value) return
    ticks.push({ value: ms, major, kind, year, month, weekday: weekdayOf(year, month, day) })
  }

  const start = zonedParts(fromMs, timeZone)
  let [year, month, day] = [start.year, start.month, start.day]

  if (step.unit === 'minute' || step.unit === 'hour') {
    const stepMinutes = step.count * (step.unit === 'hour' ? 60 : 1)
    // Start a few hours early: when the range starts late in the first pass of a
    // repeated autumn hour, the second pass shows earlier wall times that also
    // lie in the range. A DST change never shifts the clock by more than 2 h.
    const lead = zonedParts(fromMs - LEAD_MS, timeZone)
    ;[year, month, day] = [lead.year, lead.month, lead.day]
    let minuteOfDay = Math.floor((lead.hour * 60 + lead.minute) / stepMinutes) * stepMinutes
    // A repeated hour yields each wall time twice, interleaved out of order
    // (02:00, 02:15, …, then 02:00 again) — collect first, then sort.
    const candidates: { ms: number; year: number; month: number; day: number; minuteOfDay: number }[] = []
    for (let i = 0; i < MAX_CANDIDATES; i++) {
      const instants = zonedToUtcAll(year, month, day, minuteOfDay, timeZone)
      if (instants[0] > toMs) break
      // The second pass of a repeated hour can lie past the end even when the first does not.
      for (const ms of instants) {
        if (ms <= toMs) candidates.push({ ms, year, month, day, minuteOfDay })
      }
      minuteOfDay += stepMinutes
      if (minuteOfDay >= MINUTES_PER_DAY) {
        minuteOfDay = 0;
        [year, month, day] = addDays(year, month, day, 1)
      }
    }
    candidates.sort((a, b) => a.ms - b.ms)
    for (const c of candidates) {
      const midnight = c.minuteOfDay === 0
      push(c.ms, c.year, c.month, c.day, midnight ? 'day' : 'time', midnight)
    }
    return ticks
  }

  if (step.unit === 'day' || step.unit === 'week') {
    for (let i = 0; i < MAX_CANDIDATES; i++) {
      const ms = zonedToUtc(year, month, day, 0, timeZone)
      if (ms > toMs) break
      const onStep = step.unit === 'week'
        ? weekdayOf(year, month, day) === 0
        : epochDay(year, month, day) % step.count === 0
      if (onStep) push(ms, year, month, day, 'day', day === 1)
      ;[year, month, day] = addDays(year, month, day, 1)
    }
    return ticks
  }

  // Months: the first of every month whose index is a multiple of the count.
  for (let i = 0; i < MAX_CANDIDATES; i++) {
    const ms = zonedToUtc(year, month, 1, 0, timeZone)
    if (ms > toMs) break
    if ((month - 1) % step.count === 0) push(ms, year, month, 1, 'month', month === 1)
    if (month === 12) { year += 1; month = 1 } else { month += 1 }
  }
  return ticks
}

export interface TickLabelFormatters {
  /** Clock time in the regional format, e.g. `18:00` or `06:00 PM`. */
  time: (ms: number) => string
  /** Day and month in the regional format, e.g. `02.11.` or `11/02`. */
  dayMonth: (ms: number) => string
  /** Weekday/month names in the viewer's UI language. */
  names: DateTimeNames
}

export function calendarTickLabel(tick: CalendarTick, fmt: TickLabelFormatters): string {
  if (tick.kind === 'time') return fmt.time(tick.value)
  if (tick.kind === 'month') {
    const month = fmt.names.monthsShort.split('|')[tick.month - 1] ?? String(tick.month)
    return `${month} ${tick.year}`
  }
  const weekday = fmt.names.weekdaysTwo.split('|')[tick.weekday] ?? ''
  const date = fmt.dayMonth(tick.value)
  return weekday ? `${weekday} ${date}` : date
}
