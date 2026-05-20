/**
 * Tests for useTimeFilterPayload.
 *
 * timeFilterToPayload (existing) — covered by integration tests via the
 * RingBufferView time-filter spec.
 *
 * entryInTimeWindow (new — bug fix) — decides whether a live WebSocket entry
 * should be shown given the active TimeFilterPopover state. Background:
 * before this helper, RingBufferView.onLiveEntry enqueued every WS entry
 * unconditionally, so a fixed past window or a point ± span window did NOT
 * produce a static table — live pushes kept appearing on top.
 *
 * Semantics:
 *   - No filter → pass through.
 *   - Range mode: entry.ts within [from, to]; empty bound = unconstrained
 *     on that side; relative duration bounds ({seconds, sign}) are resolved
 *     against nowMs at call time.
 *   - Point mode: entry.ts within [point - span, point + span]; missing
 *     span collapses to a single instant.
 *   - Entries without a parseable ts pass through (we can't decide).
 */
import { describe, it, expect } from 'vitest'
import { entryInTimeWindow } from '@/composables/useTimeFilterPayload'

const tsAt = (year, month, day, hour, min, sec = 0) =>
  new Date(Date.UTC(year, month - 1, day, hour, min, sec)).toISOString()

describe('entryInTimeWindow — no filter', () => {
  it('returns true when filter is null', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0) }, null)).toBe(true)
  })

  it('returns true when filter is undefined', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0) }, undefined)).toBe(true)
  })

  it('returns true when filter has no usable bounds', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0) }, { mode: 'range' })).toBe(true)
  })
})

describe('entryInTimeWindow — range mode with absolute Date bounds', () => {
  const from = new Date('2026-05-13T10:00:00Z')
  const to = new Date('2026-05-13T11:00:00Z')
  const filter = { mode: 'range', from, to }

  it('passes an entry inside the window', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 10, 30) }, filter)).toBe(true)
  })

  it('passes at the lower boundary (inclusive)', () => {
    expect(entryInTimeWindow({ ts: from.toISOString() }, filter)).toBe(true)
  })

  it('passes at the upper boundary (inclusive)', () => {
    expect(entryInTimeWindow({ ts: to.toISOString() }, filter)).toBe(true)
  })

  it('drops an entry before `from`', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 9, 0) }, filter)).toBe(false)
  })

  it('drops an entry after `to`', () => {
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0) }, filter)).toBe(false)
  })

  it('CRITICAL: a "now"-stamped live entry is dropped when the window is closed in the past', () => {
    // This is the user-visible bug: with from/to set to a fixed past
    // interval, live WS entries (timestamp ≈ now) kept appearing on top.
    // The matcher must reject them so the table stays static.
    const liveNowIso = new Date('2026-05-13T20:00:00Z').toISOString()
    expect(entryInTimeWindow({ ts: liveNowIso }, filter)).toBe(false)
  })
})

describe('entryInTimeWindow — range mode with only one bound', () => {
  it('only `from` (Date) — entries after it pass, before it are dropped', () => {
    const filter = { mode: 'range', from: new Date('2026-05-13T10:00:00Z') }
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 11, 0) }, filter)).toBe(true)
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 9, 0) }, filter)).toBe(false)
  })

  it('only `to` (Date) — entries before it pass, after it are dropped', () => {
    const filter = { mode: 'range', to: new Date('2026-05-13T11:00:00Z') }
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 10, 30) }, filter)).toBe(true)
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0) }, filter)).toBe(false)
  })
})

describe('entryInTimeWindow — range mode with relative duration bounds', () => {
  // Anchor "now" so the test is deterministic.
  const nowMs = new Date('2026-05-13T20:00:00Z').getTime()

  it('relative `from` (-1h) — entries "now" pass (sliding window, upper-open)', () => {
    const filter = { mode: 'range', from: { seconds: 3600, sign: -1 } }
    expect(entryInTimeWindow({ ts: new Date(nowMs).toISOString() }, filter, nowMs)).toBe(true)
  })

  it('relative `from` (-1h) — entries 2h ago are dropped', () => {
    const filter = { mode: 'range', from: { seconds: 3600, sign: -1 } }
    expect(
      entryInTimeWindow({ ts: new Date(nowMs - 2 * 3600 * 1000).toISOString() }, filter, nowMs),
    ).toBe(false)
  })

  it('relative `from`+`to` both negative — closed past sliding window drops live "now"', () => {
    // [-1h .. -10min] — starts 1h ago, ends 10min ago. Live entries (now)
    // are AFTER `to` and must not appear.
    const filter = {
      mode: 'range',
      from: { seconds: 3600, sign: -1 },
      to: { seconds: 600, sign: -1 },
    }
    expect(entryInTimeWindow({ ts: new Date(nowMs).toISOString() }, filter, nowMs)).toBe(false)
    // Entry 30 min ago is inside the window.
    expect(
      entryInTimeWindow({ ts: new Date(nowMs - 30 * 60 * 1000).toISOString() }, filter, nowMs),
    ).toBe(true)
  })
})

describe('entryInTimeWindow — point ± span', () => {
  const point = new Date('2026-05-13T12:00:00Z')

  it('passes an entry inside [point-span, point+span]', () => {
    const filter = { mode: 'point', point, span: { seconds: 600, sign: 1 } }
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 5) }, filter)).toBe(true)
  })

  it('passes at both boundaries (inclusive)', () => {
    const filter = { mode: 'point', point, span: { seconds: 600, sign: 1 } }
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 11, 50) }, filter)).toBe(true)
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 10) }, filter)).toBe(true)
  })

  it('drops entries outside the window', () => {
    const filter = { mode: 'point', point, span: { seconds: 600, sign: 1 } }
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 13, 0) }, filter)).toBe(false)
    expect(entryInTimeWindow({ ts: tsAt(2026, 5, 13, 11, 0) }, filter)).toBe(false)
  })

  it('CRITICAL: a "now"-stamped live entry is dropped when point+span is in the past', () => {
    const filter = { mode: 'point', point, span: { seconds: 600, sign: 1 } }
    const liveNowIso = new Date('2026-05-13T20:00:00Z').toISOString()
    expect(entryInTimeWindow({ ts: liveNowIso }, filter)).toBe(false)
  })

  it('missing or zero span collapses into a single-instant window', () => {
    expect(entryInTimeWindow({ ts: point.toISOString() }, { mode: 'point', point })).toBe(true)
    expect(
      entryInTimeWindow({ ts: tsAt(2026, 5, 13, 12, 0, 1) }, { mode: 'point', point }),
    ).toBe(false)
  })
})

describe('entryInTimeWindow — defensive', () => {
  it('passes an entry with no ts (cannot decide → keep)', () => {
    const filter = { mode: 'range', from: new Date('2026-05-13T10:00:00Z') }
    expect(entryInTimeWindow({ id: 1 }, filter)).toBe(true)
  })

  it('passes an entry with an unparseable ts (cannot decide → keep)', () => {
    const filter = { mode: 'range', from: new Date('2026-05-13T10:00:00Z') }
    expect(entryInTimeWindow({ ts: 'nope' }, filter)).toBe(true)
  })
})
