/**
 * Tests for useSegmentProblems (#919/#938).
 *
 * The composable is the single source of truth for the RingBuffer
 * segment-problem view shared by the segment dialog (SegmentStatsPanel) and
 * the dashboard card (RingBufferCard): problem detection, counts, the
 * canonical problem summary, and the per-segment tooltip.
 *
 * `useSegmentProblems()` calls `useI18n()`, so it must run inside a Vue setup
 * context — we mount a throwaway component and expose the returned API.
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createTestI18n } from '../helpers/createTestI18n'
import { useSegmentProblems, isSegmentProblem, PROBLEM_RECOVERY } from '@/composables/useSegmentProblems'

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

const seg = (over = {}) => ({ integrity_status: 'ok', recovery_status: 'none', ...over })

describe('isSegmentProblem / PROBLEM_RECOVERY', () => {
  it('exposes the recovery states that count as a problem', () => {
    expect([...PROBLEM_RECOVERY].sort()).toEqual(['dirty_wal', 'pending', 'quarantined'])
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
})

describe('problemCounts', () => {
  it('returns all-zero counts for healthy segments', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg(), seg()])).toEqual({ corrupt: 0, quarantined: 0, pending: 0, dirtyWal: 0, total: 0 })
  })

  it('counts corrupt integrity independently of recovery', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ integrity_status: 'corrupt' })])).toEqual({
      corrupt: 1, quarantined: 0, pending: 0, dirtyWal: 0, total: 1,
    })
  })

  it('counts each recovery anomaly', () => {
    const { problemCounts } = useApi()
    expect(problemCounts([seg({ recovery_status: 'quarantined' })]).quarantined).toBe(1)
    expect(problemCounts([seg({ recovery_status: 'pending' })]).pending).toBe(1)
    expect(problemCounts([seg({ recovery_status: 'dirty_wal' })]).dirtyWal).toBe(1)
  })

  it('mixes corrupt + recovery and counts distinct problem segments once in total', () => {
    const { problemCounts } = useApi()
    const counts = problemCounts([
      seg(),
      seg({ integrity_status: 'corrupt', recovery_status: 'quarantined' }),
      seg({ recovery_status: 'pending' }),
    ])
    expect(counts).toEqual({ corrupt: 1, quarantined: 1, pending: 1, dirtyWal: 0, total: 2 })
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
})
