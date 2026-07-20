<template>
  <!--
    Segment-Status/Stats-Panel (#938/#919). Rendert ausschließlich aus dem
    segmentierten Store (``store = {common, backend_extra}``). Der Aufrufer
    (RingBufferView) gibt ``store`` nur weiter, wenn es != null ist — im
    Legacy-Modus wird diese Sektion daher gar nicht gemountet.

    Nach Nutzer-Feedback (#938): gesunde Segmente werden schlank dargestellt
    (nur Kern-Infos). Integrität/Wiederherstellung sind KEINE Dauer-Spalten mehr.
    Anomalien erscheinen als Warn-Badge am betroffenen Segment plus einer
    kompakten Probleme-Zeile oben; Details stehen im Tooltip.
  -->
  <section class="flex flex-col gap-4" data-testid="segment-stats-panel">
    <!-- Einstieg in den Migrations-Assistenten (#966): sichtbar, solange die
         Legacy-Quelle existiert – auch bei Entscheidung skipped/keep (beide
         revidierbar). Admin-only, weil die Migration-API admin-only ist. -->
    <div v-if="showMigrationEntry" class="flex justify-end">
      <button type="button" class="btn-secondary btn-sm" data-testid="segment-open-migration" @click="$emit('open-migration')">
        {{ $t('ringbuffer.migration.assistantButton') }}
      </button>
    </div>

    <!-- Überblick -->
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3" data-testid="segment-overview">
      <div class="flex flex-col gap-0.5">
        <span class="text-xs text-slate-500">{{ $t('ringbuffer.segmentTotalSize') }}</span>
        <span class="text-sm font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="segment-total-size">{{ totalSize }}</span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="text-xs text-slate-500">{{ $t('ringbuffer.segmentCount') }}</span>
        <span class="text-sm font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="segment-count">{{ segmentCount }}</span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="text-xs text-slate-500">{{ $t('ringbuffer.segmentActive') }}</span>
        <span class="text-sm font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="segment-active">{{ activeSegmentLabel }}</span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="text-xs text-slate-500">{{ $t('ringbuffer.segmentOldestEvent') }}</span>
        <span class="text-sm font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="segment-oldest">{{ oldest }}</span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="text-xs text-slate-500">{{ $t('ringbuffer.segmentNewestEvent') }}</span>
        <span class="text-sm font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="segment-newest">{{ newest }}</span>
      </div>
    </div>

    <!-- Kompakte Probleme-Zeile (#938): fasst alle Segment-Anomalien zusammen. -->
    <div
      v-if="problemSummary"
      class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
      data-testid="segment-problems"
    >
      {{ problemSummary }}
    </div>

    <!-- Betriebs-Hinweise -->
    <div v-if="operationalNotices.length" class="flex flex-col gap-2" data-testid="segment-notices">
      <div
        v-for="notice in operationalNotices"
        :key="notice.key"
        :class="[
          'rounded-md border px-3 py-2 text-xs',
          notice.tone === 'warn'
            ? 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300'
            : 'border-slate-200 bg-slate-500/5 text-slate-600 dark:border-slate-700 dark:text-slate-300',
        ]"
        :data-testid="`segment-notice-${notice.key}`"
      >
        {{ notice.text }}
      </div>
    </div>

    <!-- WAL / Checkpoint Zusammenfassung -->
    <div class="text-xs text-slate-500 flex flex-wrap items-center gap-x-4 gap-y-1" data-testid="segment-wal">
      <span class="font-medium">{{ $t('ringbuffer.walCheckpointTitle') }}</span>
      <span data-testid="segment-wal-size">
        {{ $t('ringbuffer.walSize') }}
        <span class="tabular-nums text-slate-700 dark:text-slate-300">{{ walSize }}</span>
      </span>
      <span v-if="checkpointPending > 0" data-testid="segment-checkpoint-pending">
        {{ $t('ringbuffer.checkpointPending') }}
        <span class="tabular-nums">{{ checkpointPending }}</span>
      </span>
      <span v-if="lastCheckpointAt" data-testid="segment-last-checkpoint">
        {{ $t('ringbuffer.lastCheckpoint') }}
        <span class="tabular-nums text-slate-700 dark:text-slate-300">{{ lastCheckpointAt }}</span>
      </span>
    </div>

    <!-- Segment-Liste — gesunde Segmente schlank: nur Kern-Infos. -->
    <div class="table-wrap overflow-x-auto" data-testid="segment-list">
      <h4 class="sr-only">{{ $t('ringbuffer.segmentsListTitle') }}</h4>
      <table class="table text-xs">
        <thead>
          <tr>
            <th>{{ $t('ringbuffer.segmentColId') }}</th>
            <th>{{ $t('ringbuffer.segmentColStatus') }}</th>
            <th class="text-right">{{ $t('ringbuffer.segmentColRows') }}</th>
            <th class="text-right">{{ $t('ringbuffer.segmentColSize') }}</th>
            <th>{{ $t('ringbuffer.segmentColTimespan') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="seg in segments" :key="seg.segment_id" data-testid="segment-row" :data-segment-id="seg.segment_id">
            <td class="font-mono tabular-nums text-slate-500">{{ seg.segment_id }}</td>
            <td>
              <div class="flex items-center gap-1.5 flex-wrap">
                <Badge
                  :variant="statusVariant(seg.status)"
                  size="xs"
                  dot
                  :title="seg.status === 'quarantined' ? $t('ringbuffer.segmentQuarantineExplain') : null"
                >{{ statusLabel(seg.status) }}</Badge>
                <!-- Warn-Badge nur bei Anomalie; Details im Tooltip. -->
                <Badge
                  v-if="segmentProblemTitle(seg)"
                  variant="danger"
                  size="xs"
                  data-testid="segment-warn-badge"
                  :title="segmentProblemTitle(seg)"
                >{{ $t('ringbuffer.segmentProblemBadge') }}</Badge>
              </div>
              <span
                v-if="seg.status === 'legacy'"
                class="block mt-0.5 text-[11px] text-slate-500"
                data-testid="segment-legacy-note"
              >{{ $t('ringbuffer.segmentLegacyNote') }}</span>
            </td>
            <td class="text-right tabular-nums text-slate-600 dark:text-slate-300" data-testid="segment-row-count">{{ fmtSegmentRowCount(seg) }}</td>
            <td class="text-right tabular-nums text-slate-600 dark:text-slate-300">{{ fmtBytes(seg.size_bytes) }}</td>
            <td class="text-slate-500 whitespace-nowrap">{{ fmtTimespan(seg.from_ts, seg.to_ts) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
/**
 * SegmentStatsPanel (#938/#919) — read-only Übersicht des segmentierten
 * RingBuffer-Stores. Reine Präsentation: nimmt ``store`` (das
 * ``{common, backend_extra}``-Objekt aus /stats) als Prop und leitet alle
 * Anzeigen daraus ab. Segmentierung ist im aktuellen Design immer aktiv; es
 * gibt hier daher keinen Aktivierungs-Zustand.
 */
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTz } from '@/composables/useTz'
import { useSegmentProblems } from '@/composables/useSegmentProblems'
import { useLegacyMigration } from '@/composables/useLegacyMigration'
import { useAuthStore } from '@/stores/auth'
import { formatBytesBinary } from '@/utils/formatBytesBinary'
import Badge from '@/components/ui/Badge.vue'

const props = defineProps({
  // { common: {...}, backend_extra: {...} } — nie null, der Aufrufer mountet
  // die Sektion nur im segmentierten Modus.
  store: { type: Object, required: true },
})
defineEmits(['open-migration'])

// Migrations-Einstieg (#966): geteilter Assistenten-Status; admin-gated, weil
// GET /ringbuffer/migration Nicht-Admins mit 403 abweist.
const auth = useAuthStore()
const { legacy: migrationLegacy, refresh: refreshMigration } = useLegacyMigration()
const showMigrationEntry = computed(() => auth.isAdmin && migrationLegacy.value != null)
onMounted(() => {
  if (auth.isAdmin) {
    refreshMigration().catch(() => {
      // Kein Status → kein Einstiegs-Button; Fehler zeigt der Wizard selbst.
    })
  }
})

const { t } = useI18n()
const { fmtDateTime } = useTz()
const { problemSummary: buildProblemSummary, segmentProblemTitle } = useSegmentProblems()

const common = computed(() => props.store?.common ?? {})
const extra = computed(() => props.store?.backend_extra ?? {})
const segments = computed(() => (Array.isArray(extra.value.segments) ? extra.value.segments : []))

const STATUS_VARIANTS = {
  active: 'success',
  closed: 'muted',
  legacy: 'info',
  checkpoint_pending: 'warning',
  quarantined: 'danger',
}

function statusVariant(status) {
  return STATUS_VARIANTS[status] ?? 'default'
}

function statusLabel(status) {
  const map = {
    active: t('ringbuffer.segmentStatus.active'),
    closed: t('ringbuffer.segmentStatus.closed'),
    legacy: t('ringbuffer.segmentStatus.legacy'),
    checkpoint_pending: t('ringbuffer.segmentStatus.checkpoint_pending'),
    quarantined: t('ringbuffer.segmentStatus.quarantined'),
  }
  return map[status] ?? status
}

function fmtInt(n) {
  const value = Number(n)
  if (!Number.isFinite(value)) return '0'
  try {
    return new Intl.NumberFormat('de-DE').format(value)
  } catch {
    return String(value)
  }
}

function fmtSegmentRowCount(segment) {
  if (segment?.row_count_accuracy === 'unknown' || segment?.row_count == null) return '—'
  const formatted = fmtInt(segment.row_count)
  return segment.row_count_accuracy === 'estimated' ? `≈ ${formatted}` : formatted
}

function fmtBytes(rawBytes) {
  return formatBytesBinary(rawBytes)
}

function fmtTs(iso) {
  if (iso == null || iso === '') return '—'
  return fmtDateTime(iso)
}

function fmtTimespan(fromTs, toTs) {
  if (!fromTs && !toTs) return '—'
  return `${fmtTs(fromTs)} – ${fmtTs(toTs)}`
}

const totalSize = computed(() => fmtBytes(common.value.size_bytes ?? 0))
const segmentCount = computed(() => fmtInt(common.value.segment_count ?? segments.value.length))
const oldest = computed(() => fmtTs(common.value.oldest_ts))
const newest = computed(() => fmtTs(common.value.newest_ts))

const activeSegmentLabel = computed(() => {
  const id = extra.value.active_segment_id
  return id == null ? '—' : `#${id}`
})

const walSize = computed(() => fmtBytes(extra.value.wal_size_bytes ?? 0))
const checkpointPending = computed(() => Number(extra.value.checkpoint_pending ?? 0))
const lastCheckpointAt = computed(() => (extra.value.last_checkpoint_at ? fmtTs(extra.value.last_checkpoint_at) : null))

// Kanonische Probleme-Zeile (#938) — Formatierung lebt im Composable.
const problemSummary = computed(() => buildProblemSummary(segments.value))

const operationalNotices = computed(() => {
  const notices = []
  if (extra.value.retention_over_budget) {
    const reason = extra.value.retention_pressure_reason
    notices.push({
      key: 'retention',
      tone: 'warn',
      text: reason ? t('ringbuffer.retentionOverBudgetReason', { reason }) : t('ringbuffer.retentionOverBudget'),
    })
  }
  if (extra.value.storage_on_network_drive) {
    notices.push({ key: 'network', tone: 'warn', text: t('ringbuffer.storageOnNetworkDrive') })
  }
  if (extra.value.wal_checkpoint_busy) {
    notices.push({ key: 'checkpointBusy', tone: 'info', text: t('ringbuffer.checkpointBusyNotice', { n: Number(extra.value.wal_checkpoint_busy) }) })
  }
  return notices
})
</script>
