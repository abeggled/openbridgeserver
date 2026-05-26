/**
 * useTimeFilterPayload — convert the TimeFilterPopover state (#432) into
 * the backend time-filter shape:
 *
 *   { from?: iso, to?: iso,
 *     from_relative_seconds?: int, to_relative_seconds?: int }
 *
 * Date bounds → ISO strings; relative durations → signed seconds.
 * Point mode (point ± span) collapses into an absolute (from, to) pair.
 *
 * Extracted from RingBufferView.vue in #438 to keep that file small.
 * The helper is a pure function — no Vue reactivity — so it stays trivial
 * to unit-test.
 *
 * @param {{ mode?: 'range'|'point', from?: any, to?: any, point?: any, span?: any } | null} filter
 * @returns {Record<string, string | number> | null}
 */
export function timeFilterToPayload(filter) {
  if (!filter) return null
  const time = {}

  function applyBound(bound, key, relKey) {
    if (!bound) return
    if (bound instanceof Date) {
      time[key] = bound.toISOString()
    } else if (Number.isFinite(bound.seconds)) {
      time[relKey] = (bound.sign === -1 ? -1 : 1) * bound.seconds
    }
  }

  if (filter.mode === 'point') {
    const point = filter.point instanceof Date
      ? filter.point
      : (Number.isFinite(filter.point?.seconds)
          ? new Date(Date.now() + (filter.point.sign === -1 ? -1 : 1) * filter.point.seconds * 1000)
          : null)
    const spanSeconds = Number.isFinite(filter.span?.seconds) ? filter.span.seconds : 0
    if (point && spanSeconds > 0) {
      time.from = new Date(point.getTime() - spanSeconds * 1000).toISOString()
      time.to = new Date(point.getTime() + spanSeconds * 1000).toISOString()
    } else if (point) {
      time.from = point.toISOString()
      time.to = point.toISOString()
    }
  } else {
    applyBound(filter.from, 'from', 'from_relative_seconds')
    applyBound(filter.to, 'to', 'to_relative_seconds')
  }

  return Object.keys(time).length ? time : null
}

/**
 * Decide whether a live WebSocket entry should be shown given the active
 * TimeFilterPopover state. Returns true to keep, false to drop.
 *
 * Background — RingBufferView.onLiveEntry used to enqueue every WS entry
 * unconditionally. With a fixed past time window or a point ± span
 * window, that meant live pushes (timestamp ≈ now) kept appearing on top
 * even though they were clearly outside the user's configured window.
 * This helper closes that gap.
 *
 * Semantics:
 *   - No filter → pass.
 *   - Range mode: entry.ts in [from, to]; empty bound = unconstrained on
 *     that side; relative duration bounds ({seconds, sign}) resolve
 *     against `nowMs` at call time (so sliding windows keep working).
 *   - Point mode: entry.ts in [point - span, point + span]; missing span
 *     collapses to a single instant.
 *   - Entry without a parseable ts → pass (we can't decide, so don't drop).
 *
 * @param {{ ts?: string }} entry
 * @param {Record<string, any> | null | undefined} filter
 * @param {number} [nowMs=Date.now()]
 * @returns {boolean}
 */
export function entryInTimeWindow(entry, filter, nowMs = Date.now()) {
  if (!filter) return true
  const ts = entry?.ts ? Date.parse(entry.ts) : NaN
  if (!Number.isFinite(ts)) return true

  function resolveBound(bound) {
    if (!bound) return null
    if (bound instanceof Date) return bound.getTime()
    if (Number.isFinite(bound.seconds)) {
      const sign = bound.sign === -1 ? -1 : 1
      return nowMs + sign * bound.seconds * 1000
    }
    return null
  }

  if (filter.mode === 'point') {
    const pointMs = resolveBound(filter.point)
    if (pointMs === null) return true
    const spanSeconds = Number.isFinite(filter.span?.seconds) ? filter.span.seconds : 0
    const lower = pointMs - spanSeconds * 1000
    const upper = pointMs + spanSeconds * 1000
    return ts >= lower && ts <= upper
  }

  const fromMs = resolveBound(filter.from)
  const toMs = resolveBound(filter.to)
  if (fromMs !== null && ts < fromMs) return false
  if (toMs !== null && ts > toMs) return false
  return true
}
