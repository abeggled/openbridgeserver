/**
 * useSegmentProblems (#919/#938) — EINE Quelle der Wahrheit für die
 * Segment-Problem-Sicht des RingBuffers.
 *
 * Sowohl der Segment-Dialog (``SegmentStatsPanel``) als auch die
 * Dashboard-Karte (``RingBufferCard``) leiten ihre Problem-Zählung, die
 * kanonische Probleme-Zusammenfassung und den Tooltip-Detailtext hieraus ab.
 * Damit bleiben Formulierung und Logik nicht dupliziert.
 *
 * Muss aus einem Vue-Setup-Kontext aufgerufen werden (nutzt ``useI18n``).
 */
import { useI18n } from 'vue-i18n'

/** Recovery-Zustände, die ein Segment als problematisch markieren (#938). */
export const PROBLEM_RECOVERY = new Set(['quarantined', 'pending', 'dirty_wal'])

/** True, wenn das Segment nicht als gesund (integrity=ok, recovery=none) gilt. */
export function isSegmentProblem(seg) {
  return seg?.integrity_status === 'corrupt' || PROBLEM_RECOVERY.has(seg?.recovery_status)
}

export function useSegmentProblems() {
  const { t } = useI18n()

  function integrityLabel(value) {
    const map = {
      ok: t('ringbuffer.integrityStatus.ok'),
      corrupt: t('ringbuffer.integrityStatus.corrupt'),
      unknown: t('ringbuffer.integrityStatus.unknown'),
    }
    return map[value] ?? value
  }

  function recoveryLabel(value) {
    const map = {
      recovered: t('ringbuffer.recoveryStatus.recovered'),
      quarantined: t('ringbuffer.recoveryStatus.quarantined'),
      pending: t('ringbuffer.recoveryStatus.pending'),
      failed: t('ringbuffer.recoveryStatus.failed'),
      dirty_wal: t('ringbuffer.recoveryStatus.dirty_wal'),
    }
    return map[value] ?? value
  }

  /**
   * Zählt korrupte Segmente und die einzelnen Recovery-Anomalien.
   * Liefert ``{ corrupt, quarantined, pending, dirtyWal, total }``.
   */
  function problemCounts(segments) {
    const list = Array.isArray(segments) ? segments : []
    let corrupt = 0
    let quarantined = 0
    let pending = 0
    let dirtyWal = 0
    let total = 0
    for (const seg of list) {
      if (seg?.integrity_status === 'corrupt') corrupt += 1
      if (seg?.recovery_status === 'quarantined') quarantined += 1
      else if (seg?.recovery_status === 'pending') pending += 1
      else if (seg?.recovery_status === 'dirty_wal') dirtyWal += 1
      if (isSegmentProblem(seg)) total += 1
    }
    return { corrupt, quarantined, pending, dirtyWal, total }
  }

  /**
   * Kanonische Probleme-Zeile (#938): verdichtet die Anomalien zu einer
   * menschlich lesbaren Zeile, z. B. „Probleme: 1 beschädigt, 1 isoliert".
   * Leer, wenn alles gesund ist.
   */
  function problemSummary(segments) {
    const { corrupt, quarantined, pending, dirtyWal } = problemCounts(segments)
    const parts = []
    if (corrupt > 0) parts.push(t('ringbuffer.segmentProblemCorrupt', { n: corrupt }))
    if (quarantined > 0) parts.push(t('ringbuffer.segmentProblemQuarantined', { n: quarantined }))
    if (pending > 0) parts.push(t('ringbuffer.segmentProblemPending', { n: pending }))
    if (dirtyWal > 0) parts.push(t('ringbuffer.segmentProblemDirtyWal', { n: dirtyWal }))
    if (!parts.length) return ''
    return `${t('ringbuffer.segmentProblemPrefix')} ${parts.join(', ')}`
  }

  /** Tooltip-Detailtext für ein problematisches Segment (Integrität/Recovery/Grund). */
  function segmentProblemTitle(seg) {
    if (!isSegmentProblem(seg)) return null
    const parts = []
    if (seg.integrity_status && seg.integrity_status !== 'ok') {
      parts.push(`${t('ringbuffer.segmentColIntegrity')}: ${integrityLabel(seg.integrity_status)}`)
    }
    if (seg.recovery_status && seg.recovery_status !== 'none') {
      parts.push(`${t('ringbuffer.segmentColRecovery')}: ${recoveryLabel(seg.recovery_status)}`)
    }
    if (seg.quarantine_reason) parts.push(seg.quarantine_reason)
    return parts.join(' · ')
  }

  return { isSegmentProblem, problemCounts, problemSummary, segmentProblemTitle }
}
