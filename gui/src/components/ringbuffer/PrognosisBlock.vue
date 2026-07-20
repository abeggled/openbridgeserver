<template>
  <!--
    PrognosisBlock (#919/#938) — gemeinsame, menschlich formulierte RingBuffer-
    Prognose. Wird sowohl im MonitorConfigModal als auch in der Dashboard-
    RingBufferCard verwendet (DRY). Rendert abgestimmte Zeilen aus
    ``prognosis`` (stats.prognosis) plus Budget-Kontext:

      1. Durchsatz  — MiB/h + Events/h (bytes_per_hour / rows_per_hour)
      2. Rotation   — ~alle N h; Minimum aus Alter, Größe und Zeilen samt Auslöser
      3. Historie   — Budget reicht für ~X; bei vollständig unbegrenzter
                      Gesamt-Retention zusätzlich 30-Tage-/Jahreswachstum
      4. Budget-Empfehlung — Budget ≥ V für ~{segmentAgeHours}-h-Segmente; V wird
                      IM FRONTEND berechnet (bytes_per_hour * age * 3), damit Label
                      und Wert live beim Tippen zusammenpassen.

    None-robust: jedes einzelne fehlende Feld blendet nur seine eigene Zeile aus,
    ohne NaN/undefined zu rendern. Fehlen alle Raten → „läuft sich noch ein".
  -->
  <div
    class="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex flex-col gap-1.5"
    data-testid="prognosis"
  >
    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200">{{ $t('ringbuffer.prognosis.title') }}</h4>
    <p v-if="!ready" class="text-xs text-slate-500" data-testid="prognosis-warming">
      {{ $t('ringbuffer.prognosis.warming') }}
    </p>
    <template v-else>
      <p v-if="rateLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-rate">{{ rateLine }}</p>
      <p v-if="rotationLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-rotation">{{ rotationLine }}</p>
      <p v-if="historyLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-history">{{ historyLine }}</p>
      <p
        v-if="unboundedGrowthLine"
        class="text-xs font-medium text-amber-700 dark:text-amber-300"
        data-testid="prognosis-unbounded-growth"
      >
        {{ unboundedGrowthLine }}
      </p>
      <p v-if="budgetLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-budget">{{ budgetLine }}</p>
    </template>
  </div>
</template>

<script setup>
/**
 * PrognosisBlock — gemeinsame RingBuffer-Prognose-Anzeige (#919/#938).
 *
 * Extrahiert aus MonitorConfigModal + RingBufferCard, damit die Formatierung
 * (Durchsatz, Rotation, Historie, Budget-Empfehlung) nur an EINER Stelle lebt.
 * Die Komponente hält keinen State — sie leitet alles aus den Props ab.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatBytesBinary } from '@/utils/formatBytesBinary'

const { t } = useI18n()

const props = defineProps({
  // stats.prognosis: { bytes_per_hour, rows_per_hour, avg_segment_seconds,
  //   estimated_retention_seconds, effective_segment_max_bytes }.
  prognosis: { type: Object, default: null },
  // Aktuell konfiguriertes Segment-Alter in Stunden. null → Budget-Zeile weg
  // (der Consumer kennt kein Segment-Alter).
  segmentAgeHours: { type: [Number, String], default: null },
  // Effektive weitere Rotationsschwellen. undefined nutzt als Abwärtskompatibilität
  // den Prognosewert; null bedeutet ausdrücklich: dieser Trigger ist deaktiviert.
  segmentMaxBytes: { type: [Number, String], default: undefined },
  segmentMaxRows: { type: [Number, String], default: undefined },
  // Größen-Budget (stats.max_file_size_bytes) für die Historie-Zeile. null →
  // Budget unbegrenzt.
  maxFileSizeBytes: { type: [Number, String], default: null },
  // Nur true, wenn ALLE Gesamtgrenzen deaktiviert sind. undefined bewahrt die
  // bisherige Anzeige für Consumer, die dieses neue Signal noch nicht liefern.
  retentionUnbounded: { type: Boolean, default: undefined },
})

// 3-Segment-Mindestregel: das Budget sollte mindestens drei Segmente fassen,
// damit die Rotation sinnvoll greifen kann (deckungsgleich mit der Backend-
// Ableitung RETENTION_SEGMENT_RATIO).
const MIN_SEGMENTS = 3
const MIB = 1024 * 1024

function posNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : null
}

function fmtNum(value, digits = 0) {
  try {
    return new Intl.NumberFormat('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
  } catch {
    return String(value)
  }
}

function formatBytesCompact(value) {
  return formatBytesBinary(value).replace(/([,.])0 (?=[KMGT]iB$)/, ' ')
}

/** Formatiert Sekunden menschlich als „~X h" bzw. „~X Tage" (h<48 → Stunden). */
function humanDuration(seconds) {
  const s = posNumber(seconds)
  if (s === null) return null
  const hours = s / 3600
  if (hours < 48) return t('ringbuffer.prognosis.hours', { n: fmtNum(hours, hours < 10 ? 1 : 0) })
  return t('ringbuffer.prognosis.days', { n: fmtNum(hours / 24, 0) })
}

// Durchsatz ist die Basis der Prognose: alle Raten fehlen → „läuft sich ein".
const ready = computed(() => {
  const p = props.prognosis
  if (!p) return false
  return posNumber(p.bytes_per_hour) !== null || posNumber(p.rows_per_hour) !== null || posNumber(p.avg_segment_seconds) !== null
})

// 1. Durchsatz.
const rateLine = computed(() => {
  const bytesPerHour = posNumber(props.prognosis?.bytes_per_hour)
  const rowsPerHour = posNumber(props.prognosis?.rows_per_hour)
  if (bytesPerHour === null || rowsPerHour === null) return ''
  return t('ringbuffer.prognosis.rate', {
    mib: fmtNum(bytesPerHour / MIB, 1),
    rows: fmtNum(rowsPerHour, 0),
  })
})

// 2. Rotation — erwartetes Intervall als Minimum aller wirksamen Trigger.
const rotationLine = computed(() => {
  const age = posNumber(props.segmentAgeHours) // Stunden
  const rawCap = props.segmentMaxBytes !== undefined
    ? props.segmentMaxBytes
    : props.prognosis?.effective_segment_max_bytes
  const cap = posNumber(rawCap) // Bytes
  const rowCap = posNumber(props.segmentMaxRows)
  const bytesPerHour = posNumber(props.prognosis?.bytes_per_hour)
  const rowsPerHour = posNumber(props.prognosis?.rows_per_hour)
  const fillHours = cap !== null && bytesPerHour !== null ? cap / bytesPerHour : null
  const rowHours = rowCap !== null && rowsPerHour !== null ? rowCap / rowsPerHour : null
  const candidates = [
    { kind: 'age', hours: age },
    { kind: 'size', hours: fillHours },
    { kind: 'rows', hours: rowHours },
  ].filter((candidate) => candidate.hours !== null)
  if (!candidates.length) return ''
  const winner = candidates.reduce((first, candidate) => candidate.hours < first.hours ? candidate : first)
  const base = t('ringbuffer.prognosis.rotationWithTrigger', {
    hours: fmtNum(winner.hours, winner.hours < 10 ? 1 : 0),
    trigger: t(`ringbuffer.prognosis.trigger.${winner.kind}`),
  })
  if (winner.kind === 'size' && cap !== null && age !== null) {
    return base + t('ringbuffer.prognosis.rotationSizeCap', {
      cap: fmtNum(cap / MIB, cap / MIB < 10 ? 1 : 0),
      age: fmtNum(age, age < 10 ? 1 : 0),
    })
  }
  return base
})

// 3. Historie — Budget reicht für ~X. Kein Budget → „unbegrenzt".
const historyLine = computed(() => {
  const budget = posNumber(props.maxFileSizeBytes)
  if (budget === null) {
    if (props.retentionUnbounded === false) return t('ringbuffer.prognosis.historyOtherLimits')
    return t('ringbuffer.prognosis.historyUnlimited')
  }
  const dur = humanDuration(props.prognosis?.estimated_retention_seconds)
  if (dur === null) return ''
  return t('ringbuffer.prognosis.history', { budget: formatBytesBinary(budget), duration: dur })
})

// Bei bewusst unbegrenzter Gesamt-Retention die Konsequenz quantifizieren.
const unboundedGrowthLine = computed(() => {
  if (props.retentionUnbounded !== true) return ''
  const bytesPerHour = posNumber(props.prognosis?.bytes_per_hour)
  if (bytesPerHour === null) return ''
  return t('ringbuffer.prognosis.unboundedGrowth', {
    month: formatBytesCompact(bytesPerHour * 24 * 30),
    year: formatBytesCompact(bytesPerHour * 24 * 365),
  })
})

// 4. Budget-Empfehlung — V IM FRONTEND berechnet (bytes_per_hour * age * 3),
// damit Label (age) und Wert live beim Tippen zusammenpassen.
const budgetLine = computed(() => {
  const age = posNumber(props.segmentAgeHours)
  const bytesPerHour = posNumber(props.prognosis?.bytes_per_hour)
  if (age === null || bytesPerHour === null) return ''
  const recommended = bytesPerHour * age * MIN_SEGMENTS
  return t('ringbuffer.prognosis.budget', {
    age: fmtNum(age, age < 10 ? 1 : 0),
    budget: formatBytesBinary(recommended),
    segments: MIN_SEGMENTS,
  })
})
</script>
