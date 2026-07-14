/**
 * Tests for useSegmentProblems (#919/#938).
 *
 * The composable is the single source of truth for the RingBuffer
 * segment-problem view shared by the segment dialog (SegmentStatsPanel) and
 * the dashboard card (RingBufferCard): problem detection, counts, the
 * canonical problem summary, and the per-segment tooltip.
 *
 * `useSegmentProblems()` calls `useI18n()`, so it must run inside a Vue setup
 * context – we mount a throwaway component and expose the returned API.
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createTestI18n } from '../helpers/createTestI18n'
import { useSegmentProblems, isSegmentProblem, PROBLEM_RECOVERY, PROBLEM_STATUS } from '@/composables/useSegmentProblems'

function useApi() {
  let api
  const Host = defineComponent({
    setup() {
      api = useSegmentProblems()
      return () => null
    },
  })
  mount(Host, { global: { plugins: [createTestI18n()] } })
  return api
}

const seg = (over = {}) => ({ status: 'closed', integrity_status: 'ok', recovery_status: 'none', ...over })

describe('isSegmentProblem / PROBLEM_RECOVERY / PROBLEM_STATUS', () => {
  it('exposes the recovery states that count as a problem', () => {
    expect([...PROBLEM_RECOVERY].sort()).toEqual(['dirty_wal', 'pending', 'quarantined'])
  })

  it('exposes the segment states that count as a problem', () => {
    expect([...PROBLEM_STATUS].sort()).toEqual(['checkpoint_pending', 'quarantined'])
  })

  it('flags corrupt integrity or a problematic recovery state', () => {
    expect(isSegmentProblem(seg())).toBe(false)
    expect(isSegmentProblem(seg({ integrity_status: 'corrupt' }))).toBe(true)
    expect(isSegmentProblem(seg({ recovery_status: 'quarantined' }))).toBe(true)
    expect(isSegmentProblem(seg({ recovery_status: 'pending' }))).toBe(true)
    expect(isSegmentProblem(seg({ recovery_status: 'dirty_wal' }))).toBe(true)
    expect(isSegmentProblem(seg({ recovery_status: 'recovered' }))).toBe(false)
    expect(isSegmentProblem(undefined)).toBe(false)
  })

  it('flags a problematic segment status even when recovery/integrity look healthy (#951)', () => {
    // A busy WAL checkpoint leaves the segment on status=checkpoint_pending
    // while recovery_status stays "none" – retention is blocked, so it is a problem.
    expect(isSegmentProblem(seg({ status: 'checkpoint_pending' }))).toBe(true)
    expect(isSegmentProblem(seg({ status: 'quarantined' }))).toBe(true)
    expect(isSegmentProblem(seg({ status: 'active' }))).toBe(false)
  })
})

describe('problemCounts', () => {
  it('returns all-zero counts for healthy segments', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg(), seg()])).toEqual({ corrupt: 0, quarantined: 0, pending: 0, dirtyWal: 0, checkpointPending: 0, total: 0 })
  })

  it('counts corrupt integrity independently of recovery', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ integrity_status: 'corrupt' })])).toEqual({
      corrupt: 1, quarantined: 0, pending: 0, dirtyWal: 0, checkpointPending: 0, total: 1,
    })
  })

  it('counts each recovery anomaly', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ recovery_status: 'quarantined' })]).quarantined).toBe(1)
    expect(problemCounts([seg({ recovery_status: 'pending' })]).pending).toBe(1)
    expect(problemCounts([seg({ recovery_status: 'dirty_wal' })]).dirtyWal).toBe(1)
  })

  it('counts a checkpoint_pending status as a problem (#951)', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ status: 'checkpoint_pending' })])).toEqual({
      corrupt: 0, quarantined: 0, pending: 0, dirtyWal: 0, checkpointPending: 1, total: 1,
    })
  })

  it('counts a quarantined status via the same quarantined bucket (#951)', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ status: 'quarantined' })])).toEqual({
      corrupt: 0, quarantined: 1, pending: 0, dirtyWal: 0, checkpointPending: 0, total: 1,
    })
  })

  it('does not double-count a segment quarantined by both status and recovery_status (#951)', () => {
    const { problemCounts } = useApi()
    const counts = problemCounts([seg({ status: 'quarantined', recovery_status: 'quarantined' })])
    expect(counts.quarantined).toBe(1)
    expect(counts.total).toBe(1)
  })

  it('mixes corrupt + recovery and counts distinct problem segments once in total', () => {
    const { problemCounts } = useApi()
    const counts = problemCounts([
      seg(),
      seg({ integrity_status: 'corrupt', recovery_status: 'quarantined' }),
      seg({ recovery_status: 'pending' }),
    ])
    expect(counts).toEqual({ corrupt: 1, quarantined: 1, pending: 1, dirtyWal: 0, checkpointPending: 0, total: 2 })
  })

  it('tolerates non-array input', () => {
    const { problemCounts } = useApi()
    expect(problemCounts(null).total).toBe(0)
  })
})

describe('problemSummary', () => {
  it('is empty when there are no problems', () => {
    const { problemSummary } = useApi()
    expect(problemSummary([seg(), seg()])).toBe('')
  })

  it('produces the canonical wording (de) with the prefix and comma-joined parts', () => {
    const { problemSummary } = useApi()
    const summary = problemSummary([
      seg({ integrity_status: 'corrupt' }),
      seg({ recovery_status: 'quarantined' }),
    ])
    expect(summary).toBe('Probleme: 1 beschädigt, 1 isoliert')
  })

  it('includes pending and dirty-wal wording', () => {
    const { problemSummary } = useApi()
    const summary = problemSummary([
      seg({ recovery_status: 'pending' }),
      seg({ recovery_status: 'dirty_wal' }),
    ])
    expect(summary).toContain('Wiederherstellung ausstehend')
    expect(summary).toContain('unsauberes WAL')
  })

  it('includes checkpoint-pending wording (#951)', () => {
    const { problemSummary } = useApi()
    const summary = problemSummary([seg({ status: 'checkpoint_pending' })])
    expect(summary).toContain('Checkpoint ausstehend')
  })
})

describe('segmentProblemTitle', () => {
  it('is null for a healthy segment', () => {
    const { segmentProblemTitle } = useApi()
    expect(segmentProblemTitle(seg())).toBe(null)
  })

  it('joins integrity, recovery and the quarantine reason', () => {
    const { segmentProblemTitle } = useApi()
    const title = segmentProblemTitle(seg({
      integrity_status: 'corrupt',
      recovery_status: 'quarantined',
      quarantine_reason: 'checksum mismatch',
    }))
    expect(title).toContain('checksum mismatch')
    expect(title).toContain(' · ')
  })

  it('reports a checkpoint_pending status even when recovery/integrity are healthy (#951)', () => {
    const { segmentProblemTitle } = useApi()
    const title = segmentProblemTitle(seg({ status: 'checkpoint_pending' }))
    expect(title).toContain('Status')
    expect(title).toContain('Checkpoint ausstehend')
  })
})

describe('retentionSignal (#919/#938)', () => {
  const stats = (over = {}) => ({
    max_age: null,
    prognosis: { estimated_retention_seconds: 3 * 24 * 3600, bytes_per_hour: 50 * 1024 * 1024 },
    store: { backend_extra: { retention_over_budget: false } },
    ...over,
  })

  it('is silent in normal operation – a full budget is not an alarm', () => {
    const { retentionSignal } = useApi()
    expect(retentionSignal(stats()).level).toBe('none')
  })

  it('returns a RED error (Fall B) when the budget floor is breached', () => {
    const { retentionSignal } = useApi()
    const sig = retentionSignal(stats({ store: { backend_extra: { retention_over_budget: true } } }))
    expect(sig.level).toBe('error')
    expect(sig.text).toContain('Budget zu klein')
  })

  it('returns an AMBER warn (Fall A) with a budget recommendation when retention is below the max_age target', () => {
    const { retentionSignal } = useApi()
    const sig = retentionSignal(stats({
      max_age: 5 * 24 * 3600,
      prognosis: { estimated_retention_seconds: 12 * 3600, bytes_per_hour: 50 * 1024 * 1024 },
    }))
    expect(sig.level).toBe('warn')
    expect(sig.text).toContain('Retention unter Ziel')
    expect(sig.text).toContain('≥') // Budget-Empfehlung
    expect(sig.text).not.toContain('NaN')
  })

  it('omits the budget recommendation when bytes_per_hour is unknown', () => {
    const { retentionSignal } = useApi()
    const sig = retentionSignal(stats({
      max_age: 5 * 24 * 3600,
      prognosis: { estimated_retention_seconds: 12 * 3600, bytes_per_hour: null },
    }))
    expect(sig.level).toBe('warn')
    expect(sig.text).toContain('Retention unter Ziel')
    expect(sig.text).not.toContain('≥')
  })

  it('stays silent when the estimated retention meets or exceeds the target', () => {
    const { retentionSignal } = useApi()
    const sig = retentionSignal(stats({
      max_age: 5 * 24 * 3600,
      prognosis: { estimated_retention_seconds: 10 * 24 * 3600, bytes_per_hour: 50 * 1024 * 1024 },
    }))
    expect(sig.level).toBe('none')
  })

  it('lets Fall B (budget floor) take precedence over Fall A', () => {
    const { retentionSignal } = useApi()
    const sig = retentionSignal(stats({
      max_age: 5 * 24 * 3600,
      prognosis: { estimated_retention_seconds: 12 * 3600, bytes_per_hour: 50 * 1024 * 1024 },
      store: { backend_extra: { retention_over_budget: true } },
    }))
    expect(sig.level).toBe('error')
  })
})
