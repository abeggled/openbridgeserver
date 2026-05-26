/**
 * useTimeFilterParser — pure-function helpers for the "Smart" time-filter UX (#432).
 *
 * Why pure functions?
 *   Re-using the same parser logic across the TimeFilterPopover, future inline
 *   chip-editors and unit tests is much simpler when nothing depends on Vue
 *   reactivity. This module has zero `import` from "vue" on purpose.
 *
 * API
 * ===
 *   parseDurationToken(text) → { seconds, sign } | null
 *     Accepts: '30s', '5min', '5m', '1h', '2d', '1h10m', '1h10min', '1h10m30s'
 *     – Whitespace between segments is tolerated.
 *     – A single leading '-' or '+' is honoured; everything else is rejected.
 *     – Fractional values ('1.5h'), unknown units ('5x'), bare numbers ('30'),
 *       and mid-string signs ('1h-30m') return null.
 *
 *   parseTimePointToken(text, now = new Date()) → Date | null
 *     Accepts:
 *       1. Relative: '-1h', '+10min', '-1h30m'  → now ± duration
 *       2. Wall-clock: '14:30' or '14:30:00'    → today at that time (local TZ)
 *       3. ISO without TZ: '2026-05-11T14:30'   → local TZ
 *       4. ISO with space: '2026-05-11 14:30'   → local TZ
 *       5. ISO with explicit Z / offset         → exact instant
 *     Returns null for anything else.
 *
 *   formatDurationDeutsch(seconds) → 'Xd Yh Zmin Ws'
 *     Always drops zero segments except when seconds === 0 ("0s").
 *     Negative input is treated as |input|.
 *
 *   formatTimeFilter({ mode, from, to, point, span }) → string
 *     Topbar label, e.g. 'Letzte 1h', 'Bereich -1h … -5min', '13:50 ± 10min'.
 *     Returns 'aus' when no meaningful values are present.
 */

// ---- duration -------------------------------------------------------------

const UNIT_SECONDS = {
  s: 1,
  m: 60,
  min: 60,
  h: 3600,
  std: 3600, // Deutsch: "Stunden" — alternate writing for hours.
  d: 86400,
}

// Order matters: longer units must come first so 'min' wins over 'm' and
// 'std' wins over 's'. One segment = (number)(unit). Multiple segments may be
// concatenated with or without whitespace, e.g. '1h10min30s' or '1h 10min 30s'.
const SEGMENT_RE = /(\d+)\s*(min|std|s|m|h|d)/y

/**
 * @param {string|null|undefined} text
 * @returns {{ seconds: number, sign: -1 | 1 } | null}
 */
export function parseDurationToken(text) {
  if (text == null) return null
  const raw = String(text).trim()
  if (!raw) return null

  let sign = 1
  let rest = raw
  if (rest.startsWith('-')) {
    sign = -1
    rest = rest.slice(1).trimStart()
  } else if (rest.startsWith('+')) {
    rest = rest.slice(1).trimStart()
  }

  // Disallow mid-string signs like '1h-30m'.
  if (rest.includes('-') || rest.includes('+')) return null
  // Disallow decimal numbers like '1.5h'.
  if (rest.includes('.') || rest.includes(',')) return null

  // Walk the string segment by segment with a sticky regex.
  SEGMENT_RE.lastIndex = 0
  let total = 0
  let consumed = 0
  let matched = false
  while (SEGMENT_RE.lastIndex < rest.length) {
    // Skip whitespace between segments
    while (rest[SEGMENT_RE.lastIndex] === ' ') {
      SEGMENT_RE.lastIndex += 1
      consumed = SEGMENT_RE.lastIndex
    }
    if (SEGMENT_RE.lastIndex >= rest.length) break
    const m = SEGMENT_RE.exec(rest)
    if (!m) return null
    matched = true
    const value = Number(m[1])
    const unit = m[2]
    if (!Number.isFinite(value)) return null
    total += value * UNIT_SECONDS[unit]
    consumed = SEGMENT_RE.lastIndex
  }
  if (!matched) return null
  if (consumed !== rest.length) return null
  return { seconds: total, sign }
}

// ---- time point -----------------------------------------------------------

const ISO_WITH_TZ_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/
const ISO_NO_TZ_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/
const TIME_ONLY_RE = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/

/**
 * @param {string|null|undefined} text
 * @param {Date} [now]
 * @returns {Date | null}
 */
export function parseTimePointToken(text, now = new Date()) {
  if (text == null) return null
  const raw = String(text).trim()
  if (!raw) return null

  // 1. Relative offset (must start with + or -)
  if (raw.startsWith('+') || raw.startsWith('-')) {
    const d = parseDurationToken(raw)
    if (!d) return null
    return new Date(now.getTime() + d.sign * d.seconds * 1000)
  }

  // 2. ISO with explicit TZ — let the native parser handle it
  if (ISO_WITH_TZ_RE.test(raw)) {
    const d = new Date(raw)
    return Number.isFinite(d.getTime()) ? d : null
  }

  // 3. ISO without TZ ("YYYY-MM-DDTHH:MM" or "YYYY-MM-DD HH:MM") → local TZ
  const m = raw.match(ISO_NO_TZ_RE)
  if (m) {
    const [, y, mo, d, h, mi, s] = m
    const year = Number(y)
    const month = Number(mo)
    const day = Number(d)
    const hour = Number(h)
    const minute = Number(mi)
    const second = s != null ? Number(s) : 0
    if (month < 1 || month > 12 || day < 1 || day > 31) return null
    if (hour > 23 || minute > 59 || second > 59) return null
    const dt = new Date(year, month - 1, day, hour, minute, second)
    // Reject overflow (e.g. 2026-02-30 → 2026-03-02)
    if (dt.getMonth() !== month - 1 || dt.getDate() !== day) return null
    return dt
  }

  // 4. Wall-clock time "HH:MM" → today at that time in local TZ
  const tm = raw.match(TIME_ONLY_RE)
  if (tm) {
    const hour = Number(tm[1])
    const minute = Number(tm[2])
    const second = tm[3] != null ? Number(tm[3]) : 0
    if (hour > 23 || minute > 59 || second > 59) return null
    return new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
      hour,
      minute,
      second,
    )
  }

  return null
}

// ---- formatter ------------------------------------------------------------

/**
 * @param {number} seconds
 * @returns {string}
 */
export function formatDurationDeutsch(seconds) {
  let total = Math.abs(Math.trunc(Number(seconds) || 0))
  if (total === 0) return '0s'
  const days = Math.floor(total / 86400)
  total -= days * 86400
  const hours = Math.floor(total / 3600)
  total -= hours * 3600
  const minutes = Math.floor(total / 60)
  const secs = total - minutes * 60
  const parts = []
  if (days) parts.push(`${days}d`)
  if (hours) parts.push(`${hours}h`)
  if (minutes) parts.push(`${minutes}min`)
  if (secs) parts.push(`${secs}s`)
  return parts.join(' ')
}

function formatSignedDuration(d) {
  if (!d || !Number.isFinite(d.seconds)) return ''
  const body = formatDurationDeutsch(d.seconds)
  return d.sign < 0 ? `-${body}` : body
}

function isDuration(x) {
  return x && typeof x === 'object' && Number.isFinite(x.seconds) && (x.sign === 1 || x.sign === -1)
}

function isDate(x) {
  return x instanceof Date && Number.isFinite(x.getTime())
}

function formatBound(x) {
  if (isDuration(x)) return formatSignedDuration(x)
  if (isDate(x)) {
    const pad = (n) => String(n).padStart(2, '0')
    return `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())} ${pad(x.getHours())}:${pad(x.getMinutes())}`
  }
  return ''
}

/**
 * @param {{ mode?: 'range'|'point', from?: any, to?: any, point?: any, span?: any } | null} filter
 * @returns {string}
 */
export function formatTimeFilter(filter) {
  if (!filter || typeof filter !== 'object') return 'aus'
  const { mode, from, to, point, span } = filter
  if (mode === 'point') {
    if (!isDate(point) && !isDuration(point)) return 'aus'
    const pointLabel = isDate(point)
      ? `${String(point.getHours()).padStart(2, '0')}:${String(point.getMinutes()).padStart(2, '0')}`
      : formatBound(point)
    if (isDuration(span)) {
      return `${pointLabel} ± ${formatDurationDeutsch(span.seconds)}`
    }
    return pointLabel
  }
  // mode === 'range' (default)
  const hasFrom = isDuration(from) || isDate(from)
  const hasTo = isDuration(to) || isDate(to)
  if (!hasFrom && !hasTo) return 'aus'
  if (hasFrom && !hasTo && isDuration(from) && from.sign === -1) {
    return `Letzte ${formatDurationDeutsch(from.seconds)}`
  }
  if (hasFrom && hasTo) {
    return `Bereich ${formatBound(from)} … ${formatBound(to)}`
  }
  if (hasFrom) return `Ab ${formatBound(from)}`
  return `Bis ${formatBound(to)}`
}
