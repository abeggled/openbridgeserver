<template>
  <!--
    PrognosisBlock (#919/#938) — gemeinsame, menschlich formulierte RingBuffer-
    Prognose. Wird sowohl im MonitorConfigModal als auch in der Dashboard-
    RingBufferCard verwendet (DRY). Rendert die KOMPLETTE Prognose aus
    ``prognosis`` (stats.prognosis):

      • Titel
      • „läuft sich ein"-Hinweis, wenn Durchsatz/Segment-Takt noch fehlen
      • Rate-Zeile   (MiB/h + Events/h + „~alle N h")
      • Retention-Zeile (estimated_retention_seconds)
      • Budget-Zeile (recommended_budget_for_segment_age_bytes; nur wenn
                      segmentAgeHours gesetzt ist)

    None-robust: jedes einzelne fehlende Feld blendet nur seine eigene Zeile aus,
    ohne NaN/undefined zu rendern.
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
      <p class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-rate">{{ rateLine }}</p>
      <p v-if="retentionLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-retention">{{ retentionLine }}</p>
      <p v-if="budgetLine" class="text-xs text-slate-600 dark:text-slate-300" data-testid="prognosis-budget">{{ budgetLine }}</p>
    </template>
  </div>
</template>

<script setup>
/**
 * PrognosisBlock — gemeinsame RingBuffer-Prognose-Anzeige (#919/#938).
 *
 * Extrahiert aus MonitorConfigModal + RingBufferCard, damit die Formatierung
 * (Raten, Retention-Horizont, Budget-Empfehlung) nur an EINER Stelle lebt.
 * Die Komponente hält keinen State — sie leitet alles aus den Props ab.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  // stats.prognosis: { bytes_per_hour, rows_per_hour, avg_segment_seconds,
  //   estimated_retention_seconds, recommended_budget_for_segment_age_bytes }.
  prognosis: { type: Object, default: null },
  // Aktuell konfiguriertes Segment-Alter in Stunden. null → Budget-Zeile weg
  // (der Consumer kennt kein Segment-Alter).
  segmentAgeHours: { type: [Number, String], default: null },
})

const MIB = 1024 * 1024
const GIB = 1024 * 1024 * 1024

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

/** Formatiert Sekunden menschlich als „~X h" bzw. „~X Tage" (sinnvoll gerundet). */
function humanDuration(seconds) {
  const s = posNumber(seconds)
  if (s === null) return null
  const hours = s / 3600
  if (hours < 48) return t('ringbuffer.prognosis.hours', { n: fmtNum(hours, hours < 10 ? 1 : 0) })
  return t('ringbuffer.prognosis.days', { n: fmtNum(hours / 24, 0) })
}

// Rate-Zeile ist die Basis der Prognose; sie braucht Durchsatz + Segment-Takt.
const ready = computed(() => {
  const p = props.prognosis
  if (!p) return false
  return posNumber(p.bytes_per_hour) !== null && posNumber(p.rows_per_hour) !== null && posNumber(p.avg_segment_seconds) !== null
})

const rateLine = computed(() => {
  const p = props.prognosis
  if (!p) return ''
  const bytesPerHour = posNumber(p.bytes_per_hour)
  const rowsPerHour = posNumber(p.rows_per_hour)
  const segEverySeconds = posNumber(p.avg_segment_seconds)
  if (bytesPerHour === null || rowsPerHour === null || segEverySeconds === null) return ''
  return t('ringbuffer.prognosis.rate', {
    mib: fmtNum(bytesPerHour / MIB, 1),
    rows: fmtNum(rowsPerHour, 0),
    hours: fmtNum(segEverySeconds / 3600, segEverySeconds / 3600 < 10 ? 1 : 0),
  })
})

const retentionLine = computed(() => {
  const dur = humanDuration(props.prognosis?.estimated_retention_seconds)
  return dur ? t('ringbuffer.prognosis.retention', { duration: dur }) : ''
})

const budgetLine = computed(() => {
  const age = posNumber(props.segmentAgeHours)
  if (age === null) return ''
  const bytes = posNumber(props.prognosis?.recommended_budget_for_segment_age_bytes)
  if (bytes === null) return ''
  return t('ringbuffer.prognosis.budget', {
    age: fmtNum(age, age < 10 ? 1 : 0),
    gib: fmtNum(bytes / GIB, 1),
  })
})
</script>
