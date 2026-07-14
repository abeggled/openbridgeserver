/**
 * useSegmentProblems (#919/#938) – EINE Quelle der Wahrheit für die
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
import { formatBytesBinary } from '@/utils/formatBytesBinary'

/** Recovery-Zustände, die ein Segment als problematisch markieren (#938). */
export const PROBLEM_RECOVERY = new Set(['quarantined', 'pending', 'dirty_wal'])

/**
 * Segment-``status``-Werte, die ein Segment als problematisch markieren (#951).
 *
 * Das Backend meldet busy-Checkpoint- und Quarantäne-Zustände über
 * ``segment.status`` (``SEGMENT_STATUS_CHECKPOINT_PENDING`` /
 * ``SEGMENT_STATUS_QUARANTINED``), NICHT über ``recovery_status``. Ein Segment,
 * das nach einem belegten WAL-Checkpoint auf ``checkpoint_pending`` steht,
 * blockiert die Retention und muss daher als Problem erkannt werden – auch wenn
 * Integrität und Recovery gesund aussehen.
 */
export const PROBLEM_STATUS = new Set(['checkpoint_pending', 'quarantined'])

function posNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : null
}

/** True, wenn das Segment nicht als gesund (status/integrity/recovery unauffällig) gilt. */
export function isSegmentProblem(seg) {
  return (
    seg?.integrity_status === 'corrupt' ||
    PROBLEM_RECOVERY.has(seg?.recovery_status) ||
    PROBLEM_STATUS.has(seg?.status)
  )
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

  function statusLabel(value) {
    const map = {
      active: t('ringbuffer.segmentStatus.active'),
      closed: t('ringbuffer.segmentStatus.closed'),
      legacy: t('ringbuffer.segmentStatus.legacy'),
      checkpoint_pending: t('ringbuffer.segmentStatus.checkpoint_pending'),
      quarantined: t('ringbuffer.segmentStatus.quarantined'),
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
   * Zählt korrupte Segmente sowie die einzelnen Recovery- und Status-Anomalien.
   * ``quarantined`` bündelt beide Quellen (``recovery_status`` und ``status``),
   * zählt ein Segment aber nur einmal. Liefert
   * ``{ corrupt, quarantined, pending, dirtyWal, checkpointPending, total }``.
   */
  function problemCounts(segments) {
    const list = Array.isArray(segments) ? segments : []
    let corrupt = 0
    let quarantined = 0
    let pending = 0
    let dirtyWal = 0
    let checkpointPending = 0
    let total = 0
    for (const seg of list) {
      if (seg?.integrity_status === 'corrupt') corrupt += 1
      if (seg?.recovery_status === 'quarantined' || seg?.status === 'quarantined') quarantined += 1
      else if (seg?.recovery_status === 'pending') pending += 1
      else if (seg?.recovery_status === 'dirty_wal') dirtyWal += 1
      if (seg?.status === 'checkpoint_pending') checkpointPending += 1
      if (isSegmentProblem(seg)) total += 1
    }
    return { corrupt, quarantined, pending, dirtyWal, checkpointPending, total }
  }

  /**
   * Kanonische Probleme-Zeile (#938): verdichtet die Anomalien zu einer
   * menschlich lesbaren Zeile, z. B. „Probleme: 1 beschädigt, 1 isoliert".
   * Leer, wenn alles gesund ist.
   */
  function problemSummary(segments) {
    const { corrupt, quarantined, pending, dirtyWal, checkpointPending } = problemCounts(segments)
    const parts = []
    if (corrupt > 0) parts.push(t('ringbuffer.segmentProblemCorrupt', { n: corrupt }))
    if (quarantined > 0) parts.push(t('ringbuffer.segmentProblemQuarantined', { n: quarantined }))
    if (pending > 0) parts.push(t('ringbuffer.segmentProblemPending', { n: pending }))
    if (dirtyWal > 0) parts.push(t('ringbuffer.segmentProblemDirtyWal', { n: dirtyWal }))
    if (checkpointPending > 0) parts.push(t('ringbuffer.segmentProblemCheckpointPending', { n: checkpointPending }))
    if (!parts.length) return ''
    return `${t('ringbuffer.segmentProblemPrefix')} ${parts.join(', ')}`
  }

  /** Tooltip-Detailtext für ein problematisches Segment (Integrität/Recovery/Grund). */
  function segmentProblemTitle(seg) {
    if (!isSegmentProblem(seg)) return null
    const parts = []
    if (PROBLEM_STATUS.has(seg.status)) {
      parts.push(`${t('ringbuffer.segmentColStatus')}: ${statusLabel(seg.status)}`)
    }
    if (seg.integrity_status && seg.integrity_status !== 'ok') {
      parts.push(`${t('ringbuffer.segmentColIntegrity')}: ${integrityLabel(seg.integrity_status)}`)
    }
    if (seg.recovery_status && seg.recovery_status !== 'none') {
      parts.push(`${t('ringbuffer.segmentColRecovery')}: ${recoveryLabel(seg.recovery_status)}`)
    }
    if (seg.quarantine_reason) parts.push(seg.quarantine_reason)
    return parts.join(' · ')
  }

  /** Menschliche Dauer „~X h" (h<48) bzw. „~X Tage" – deckungsgleich mit PrognosisBlock. */
  function humanDuration(seconds) {
    const s = posNumber(seconds)
    if (s === null) return null
    const hours = s / 3600
    if (hours < 48) {
      return t('ringbuffer.prognosis.hours', { n: new Intl.NumberFormat('de-DE', { maximumFractionDigits: hours < 10 ? 1 : 0 }).format(hours) })
    }
    return t('ringbuffer.prognosis.days', { n: new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 }).format(hours / 24) })
  }

  /**
   * Retention-Signal (#919/#938) – trennt Normalbetrieb von echter Fehlanpassung.
   *
   * Ein voller Budget-Füllstand ist NORMAL (FIFO füllt das Budget immer) und
   * erzeugt KEIN Signal. Rot/Warn ist reserviert für „Durchsatz ist der Config
   * entwachsen":
   *   - Fall B (error): ``retention_over_budget`` – nicht löschbare Segmente
   *     (aktiv/pending/isoliert) sprengen das harte Byte-Budget dauerhaft.
   *   - Fall A (warn): ein Age-Ziel (``max_age``) ist gesetzt UND die datengetrieben
   *     geschätzte Retention liegt darunter → Budget zu klein für Durchsatz+Ziel.
   * Ohne ``max_age`` kann nur Fall B auslösen (bewusst fehlalarmarm).
   *
   * Liefert ``{ level: 'none'|'warn'|'error', text }`` aus dem vollen /stats-Objekt.
   */
  function retentionSignal(stats) {
    if (stats?.store?.backend_extra?.retention_over_budget) {
      return { level: 'error', text: t('ringbuffer.retentionSignal.budgetFloor') }
    }
    const maxAge = posNumber(stats?.max_age)
    const est = posNumber(stats?.prognosis?.estimated_retention_seconds)
    if (maxAge !== null && est !== null && est < maxAge) {
      const bytesPerHour = posNumber(stats?.prognosis?.bytes_per_hour)
      const params = { current: humanDuration(est), target: humanDuration(maxAge) }
      if (bytesPerHour !== null) {
        return { level: 'warn', text: t('ringbuffer.retentionSignal.belowTarget', { ...params, budget: formatBytesBinary(bytesPerHour * (maxAge / 3600)) }) }
      }
      return { level: 'warn', text: t('ringbuffer.retentionSignal.belowTargetNoBudget', params) }
    }
    return { level: 'none', text: '' }
  }

  return { isSegmentProblem, problemCounts, problemSummary, segmentProblemTitle, retentionSignal }
}
