<template>
  <Modal v-model="open" :title="$t('ringbuffer.migration.title')" max-width="2xl">
    <div class="flex flex-col gap-4" data-testid="legacy-migration-wizard">
      <!-- Abschluss-Ansicht: Job done bzw. Entscheidung keep/discard/migrated. -->
      <div v-if="finished" class="flex flex-col items-center gap-3 py-6 text-center" data-testid="wizard-finished">
        <span class="w-10 h-10 rounded-xl flex items-center justify-center text-xl bg-green-500/15">✓</span>
        <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ finishedText }}</p>
        <button type="button" class="btn-primary" data-testid="wizard-finished-close" @click="open = false">
          {{ $t('common.close') }}
        </button>
      </div>

      <template v-else>
        <!-- Schritt „Ist-Analyse" -->
        <section class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2" data-testid="wizard-analysis">
          <h4 class="text-sm font-semibold text-slate-800 dark:text-slate-100">{{ $t('ringbuffer.migration.analysisTitle') }}</h4>
          <div class="text-xs text-slate-500 flex items-center justify-between">
            <span>{{ $t('ringbuffer.migration.legacySize') }}</span>
            <span class="font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="wizard-legacy-size">{{ legacySizeText }}</span>
          </div>
          <div class="text-xs text-slate-500 flex items-center justify-between">
            <span>{{ $t('ringbuffer.migration.rowEstimate') }}</span>
            <span class="font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="wizard-row-estimate">{{ rowEstimateText }}</span>
          </div>
          <div class="text-xs text-slate-500 flex items-center justify-between">
            <span>{{ $t('ringbuffer.migration.timespan') }}</span>
            <span class="font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="wizard-timespan">{{ timespanText }}</span>
          </div>
          <div class="text-xs text-slate-500 flex items-center justify-between">
            <span>{{ $t('ringbuffer.migration.budget') }}</span>
            <span class="font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="wizard-budget">{{ budgetText }}</span>
          </div>
          <div class="text-xs text-slate-500 flex items-center justify-between">
            <span>{{ $t('ringbuffer.migration.diskFree') }}</span>
            <span class="font-medium tabular-nums text-slate-700 dark:text-slate-200" data-testid="wizard-disk-free">{{ diskFreeText }}</span>
          </div>
          <!-- Budget zu klein für verlustfreie Übernahme (deckt auch den
               10-MiB-Alt-Default ab): Faustformel 2 × Alt-DB, Absprung ins
               Konfig-Modal. Erst Budget anpassen, dann in Ruhe entscheiden. -->
          <div
            v-if="showBudgetHint"
            class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between"
            data-testid="wizard-budget-hint"
          >
            <span>{{ $t('ringbuffer.migration.budgetLossHint', { recommended: recommendedBudgetText }) }}</span>
            <button
              type="button"
              class="self-start font-medium underline underline-offset-2 sm:self-center"
              data-testid="wizard-open-config"
              @click="$emit('open-config')"
            >
              {{ $t('ringbuffer.migration.openConfig') }}
            </button>
          </div>
        </section>

        <!-- Aktions-Fehler (409 vom Backend etc.) -->
        <div v-if="actionError" class="p-3 rounded-lg text-sm bg-red-500/10 text-red-500 dark:text-red-400" data-testid="wizard-action-error">
          {{ actionError }}
        </div>

        <!-- Drei Optionen als Karten mit Konsequenz-Vorschau -->
        <div class="grid gap-3 md:grid-cols-3" data-testid="wizard-options">
          <!-- Migrieren -->
          <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2" data-testid="wizard-option-migrate">
            <h5 class="text-sm font-semibold text-slate-800 dark:text-slate-100">{{ $t('ringbuffer.migration.migrateTitle') }}</h5>
            <p class="text-xs text-slate-500">{{ $t('ringbuffer.migration.migrateBody') }}</p>
            <p
              v-if="diskVerdict"
              :class="['text-xs font-medium', diskVerdict.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400']"
              data-testid="wizard-disk-verdict"
            >
              {{ diskVerdict.text }}
            </p>

            <!-- Job-Fortschritt -->
            <div v-if="jobVisible" class="flex flex-col gap-1" data-testid="wizard-migrate-progress">
              <div class="flex items-center justify-between text-xs">
                <span class="text-slate-500">{{ jobPhaseText }}</span>
                <span class="tabular-nums text-slate-600 dark:text-slate-300" data-testid="wizard-migrate-rows">
                  {{ $t('ringbuffer.migration.progressRows', { copied: fmtInt(job?.copied_rows), total: fmtInt(job?.total_rows) }) }}
                </span>
              </div>
              <div class="h-2 rounded-full bg-slate-200 dark:bg-slate-700/60 overflow-hidden">
                <div class="h-full rounded-full bg-blue-500 transition-all" :style="{ width: `${jobProgressPercent}%` }" />
              </div>
              <p v-if="droppedRows > 0" class="text-[11px] text-slate-400" data-testid="wizard-migrate-dropped">
                {{ $t('ringbuffer.migration.progressDropped', { n: fmtInt(droppedRows) }) }}
              </p>
              <p v-if="jobFailed" class="text-xs text-red-600 dark:text-red-400" data-testid="wizard-migrate-error">
                {{ $t('ringbuffer.migration.migrateFailed', { error: job?.error ?? '' }) }}
              </p>
            </div>

            <button
              type="button"
              class="btn-primary btn-sm mt-auto"
              data-testid="wizard-migrate-start"
              :disabled="!canStartMigration"
              @click="onStartMigration"
            >
              <Spinner v-if="jobRunning" size="sm" color="white" />
              {{ jobRunning ? $t('ringbuffer.migration.migrateRunning') : $t('ringbuffer.migration.migrateStart') }}
            </button>
          </div>

          <!-- Behalten -->
          <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2" data-testid="wizard-option-keep">
            <h5 class="text-sm font-semibold text-slate-800 dark:text-slate-100">{{ $t('ringbuffer.migration.keepTitle') }}</h5>
            <p class="text-xs text-slate-500">{{ $t('ringbuffer.migration.keepBody') }}</p>
            <p v-if="keepEtaText" class="text-xs font-medium text-amber-600 dark:text-amber-400" data-testid="wizard-keep-eta">
              {{ keepEtaText }}
            </p>
            <button
              type="button"
              class="btn-secondary btn-sm mt-auto"
              data-testid="wizard-keep"
              :disabled="busy"
              @click="onKeep"
            >
              {{ $t('ringbuffer.migration.keepAction') }}
            </button>
          </div>

          <!-- Verwerfen -->
          <div class="rounded-lg border border-red-500/40 p-3 flex flex-col gap-2" data-testid="wizard-option-discard">
            <h5 class="text-sm font-semibold text-red-700 dark:text-red-300">{{ $t('ringbuffer.migration.discardTitle') }}</h5>
            <p class="text-xs text-slate-500">{{ $t('ringbuffer.migration.discardBody') }}</p>
            <p class="text-xs font-medium text-slate-600 dark:text-slate-300" data-testid="wizard-discard-freed">
              {{ $t('ringbuffer.migration.discardFreed', { size: legacySizeText }) }}
            </p>
            <button
              type="button"
              class="btn-danger btn-sm mt-auto"
              data-testid="wizard-discard"
              :disabled="busy"
              @click="showDiscardConfirm = true"
            >
              {{ $t('ringbuffer.migration.discardAction') }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </Modal>

  <ConfirmDialog
    v-model="showDiscardConfirm"
    :title="$t('ringbuffer.migration.discardConfirmTitle')"
    :message="$t('ringbuffer.migration.discardConfirmMessage', { size: legacySizeText })"
    :confirm-label="$t('ringbuffer.migration.discardConfirmLabel')"
    @confirm="onDiscard"
  />
</template>

<script setup>
/**
 * LegacyMigrationWizard (#966) — Modal des Migrations-Assistenten.
 *
 * Modal-Muster wie MonitorConfigModal: v-model steuert die Sichtbarkeit, beim
 * Öffnen wird der Status frisch geladen. Der Zustand kommt aus dem geteilten
 * ``useLegacyMigration``-Composable, damit Banner/Panel sofort mitziehen.
 *
 * Drei Wege: Migrieren (Offline-Job mit Fortschritt), Behalten (Schutz fällt,
 * FIFO darf die Alt-Historie später als Ganzes verwerfen, revidierbar) und
 * Verwerfen (sofort + endgültig, mit Bestätigung). ``open-config`` emittet
 * nach oben; der Host verdrahtet das mit dem vorhandenen MonitorConfigModal.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLegacyMigration } from '@/composables/useLegacyMigration'
import { useTz } from '@/composables/useTz'
import { formatBytesBinary } from '@/utils/formatBytesBinary'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'open-config'])

const { t } = useI18n()
const { fmtDateTime } = useTz()
const { status, decision, legacy, job, jobRunning, refresh, decide, startMigration } = useLegacyMigration()

// Budget-Alt-Default (10 MiB) vor der 100-MiB-Neuinstallations-Vorgabe (#966).
const LEGACY_DEFAULT_BUDGET_BYTES = 10 * 1024 * 1024
const SECONDS_PER_DAY = 24 * 3600

const open = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const actionError = ref(null)
const busy = ref(false)
const showDiscardConfirm = ref(false)
// „Behalten" ist revidierbar und daher NICHT terminal im Status – die
// Abschluss-Ansicht dafür lebt lokal in der Session dieses Modals.
const keepJustChosen = ref(false)

watch(open, (val) => {
  if (val) {
    actionError.value = null
    keepJustChosen.value = false
    refresh().catch(() => {
      actionError.value = t('ringbuffer.migration.loadFailed')
    })
  }
})

// ── Abschluss ──────────────────────────────────────────────────────────────
// ``phase === 'done'`` zählt nur als Abschluss, wenn KEINE Legacy-Quelle mehr übrig ist
// (#968, Codex :216): migriert ein Lauf bei mehreren attachten Legacy-DBs nur die erste,
// bleibt die Entscheidung non-terminal und ``legacy`` non-null – der Admin muss den
// nächsten Lauf (bzw. discard einer quarantänierten Quelle) aus dem Assistenten starten
// können, statt in den Abschluss-Screen gedrängt zu werden. Terminale Entscheidungen
// (migrated/discarded) und der frisch gewählte keep-Pfad schließen weiterhin sofort ab.
const finished = computed(
  () =>
    decision.value === 'migrated' ||
    decision.value === 'discarded' ||
    (job.value?.phase === 'done' && !legacy.value) ||
    keepJustChosen.value
)
const finishedText = computed(() => {
  if (decision.value === 'discarded') return t('ringbuffer.migration.discardDone')
  if (keepJustChosen.value) return t('ringbuffer.migration.keepDone')
  return t('ringbuffer.migration.migrateDone')
})

// ── Ist-Analyse ────────────────────────────────────────────────────────────
function fmtInt(n) {
  const value = Number(n)
  if (!Number.isFinite(value)) return '0'
  try {
    return new Intl.NumberFormat('de-DE').format(value)
  } catch {
    return String(value)
  }
}

const legacySizeText = computed(() => formatBytesBinary(legacy.value?.size_bytes ?? 0))
const rowEstimateText = computed(() => {
  const raw = legacy.value?.row_estimate
  if (raw === null || raw === undefined) return '–'
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? fmtInt(n) : '–'
})
const timespanText = computed(() => {
  const from = legacy.value?.from_ts
  const to = legacy.value?.to_ts
  if (!from || !to) return '–'
  return `${fmtDateTime(from)} – ${fmtDateTime(to)}`
})
const budgetBytes = computed(() => {
  const n = Number(status.value?.budget_bytes)
  return Number.isFinite(n) && n > 0 ? n : null
})
const budgetText = computed(() => (budgetBytes.value !== null ? formatBytesBinary(budgetBytes.value) : t('ringbuffer.migration.noBudget')))
const diskFreeBytes = computed(() => {
  // Nullish VOR der Number-Coercion behandeln (#968, Codex :252): der Backend liefert
  // ``null``, wenn der freie Platz unbekannt ist. ``Number(null)`` wäre 0 und würde
  // fälschlich „0 B frei" anzeigen, den Disk-Check rot färben und den Start sperren –
  // gewollt ist stattdessen: unbekannt → der Backend-Precheck entscheidet.
  const raw = status.value?.disk_free_bytes
  if (raw === null || raw === undefined) return null
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? n : null
})
const diskFreeText = computed(() => (diskFreeBytes.value !== null ? formatBytesBinary(diskFreeBytes.value) : '–'))
// Tatsächlicher Copy-Bedarf des Jobs (budget-gekapptes v2-Äquivalent der Legacy-
// Daten), nicht das volle Budget – Fallback Budget nur, wenn das Backend KEINE
// Schätzung liefert (#968). Ein finiter Wert von 0 (Codex :266) ist legitim –
// Live-Bestand + Headroom lassen keine budgetierten Alt-Zeilen übrig, die Migration
// kopiert dann 0 Bytes (drop-only) und braucht keinen Platz. Nicht auf das volle
// Budget zurückfallen und den Start grundlos sperren.
const requiredBytes = computed(() => {
  const raw = status.value?.estimated_copy_bytes
  if (raw === null || raw === undefined) return budgetBytes.value
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? n : budgetBytes.value
})
const isLegacyDefaultBudget = computed(() => budgetBytes.value === LEGACY_DEFAULT_BUDGET_BYTES)
// Faustformel verlustfreie Übernahme (#965): v2-Segmente speichern dieselben
// Events größer als die v1-Quelle (typisierte Spalten, Metadaten-Indexe;
// gemessen ~1,4× bei metadatenreichen bis ~4× bei sehr kleinen Events).
// Empfehlung: Budget ≥ 2 × Alt-Datenbank. Der Job kalibriert den realen Faktor
// vor der Kopie über ein Sample und kappt notfalls budget-genau.
const recommendedBudgetBytes = computed(() => {
  const size = Number(legacy.value?.size_bytes)
  return Number.isFinite(size) && size > 0 ? 2 * size : null
})
const recommendedBudgetText = computed(() => (recommendedBudgetBytes.value !== null ? formatBytesBinary(recommendedBudgetBytes.value) : '–'))
const showBudgetHint = computed(() => {
  if (isLegacyDefaultBudget.value) return true
  if (budgetBytes.value === null || recommendedBudgetBytes.value === null) return false
  return budgetBytes.value < recommendedBudgetBytes.value
})

// ── Migrieren ──────────────────────────────────────────────────────────────
// Muss zum Backend-Precheck ``OfflineLegacyMigrator._check_disk_free`` passen (#968): der
// lehnt einen Lauf ab, sobald ``disk_free < copy_bytes_estimate * 1.2`` – denselben
// Sicherheitsfaktor hier anwenden, sonst zeigte der Wizard einen grünen Disk-Befund (Start-
// Button aktiv), während die API den Lauf sofort mit „not enough free disk space" ablehnt.
const DISK_SAFETY_FACTOR = 1.2
// Disk-Check-Verdict: die Kopie braucht höchstens den geschätzten Copy-Bedarf
// (budget-gekapptes v2-Äquivalent der Legacy-Daten), NICHT das volle Budget –
// sonst blockiert ein großes Retention-Budget die Migration grundlos (#968).
const diskVerdict = computed(() => {
  if (diskFreeBytes.value === null || requiredBytes.value === null) return null
  const required = Math.ceil(requiredBytes.value * DISK_SAFETY_FACTOR)
  // ``>=`` spiegelt die Backend-Semantik (#968): ``_check_disk_free`` lehnt nur
  // ``disk_free < estimate * 1.2`` ab, erlaubt also GENAU den Schwellwert. Ein strikteres
  // ``>`` blockierte den Start am exakten Grenzwert, den die API akzeptiert.
  const ok = diskFreeBytes.value >= required
  const params = { free: formatBytesBinary(diskFreeBytes.value), budget: formatBytesBinary(required) }
  return { ok, text: ok ? t('ringbuffer.migration.diskOk', params) : t('ringbuffer.migration.diskLow', params) }
})

const jobFailed = computed(() => job.value?.phase === 'failed')
const jobVisible = computed(() => jobRunning.value || jobFailed.value)
const jobPhaseText = computed(() => {
  const phase = job.value?.phase
  const known = new Set(['idle', 'starting', 'precheck', 'copying', 'committing', 'done', 'failed'])
  return known.has(phase) ? t(`ringbuffer.migration.phase.${phase}`) : String(phase ?? '')
})
const jobProgressPercent = computed(() => {
  const copied = Number(job.value?.copied_rows)
  const total = Number(job.value?.total_rows)
  if (!Number.isFinite(copied) || !Number.isFinite(total) || total <= 0) return 0
  return Math.min(100, Math.max(0, (copied / total) * 100))
})
const droppedRows = computed(() => {
  const n = Number(job.value?.dropped_rows)
  return Number.isFinite(n) && n > 0 ? n : 0
})

// Start nur, solange Legacy existiert, kein Job läuft und der Disk-Check nicht
// explizit rot ist (unbekannter freier Platz → Backend-Precheck entscheidet).
const canStartMigration = computed(
  () => legacy.value != null && !jobRunning.value && !busy.value && diskVerdict.value?.ok !== false
)

/** 409-``detail`` (String) des Backends bevorzugen, sonst generische Meldung. */
function extractActionError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return error?.message || t('ringbuffer.migration.actionFailed')
}

async function onStartMigration() {
  actionError.value = null
  busy.value = true
  try {
    await startMigration()
  } catch (error) {
    actionError.value = extractActionError(error)
  } finally {
    busy.value = false
  }
}

// ── Behalten ───────────────────────────────────────────────────────────────
const keepEtaText = computed(() => {
  const s = status.value
  if (!s) return null
  // Fehlende ETA (kein Budget / zu wenig Prognosedaten → Backend liefert ``null``)
  // NICHT als sofortigen Druck lesen (#968, Codex :339): ``Number(null)`` wäre 0 und
  // meldete fälschlich „Budget bereits überschritten". ``over_budget`` ist das
  // autoritative Sofort-Signal; eine echte ETA von 0 zählt ebenfalls.
  const raw = s.estimated_seconds_until_budget
  const eta = raw === null || raw === undefined ? null : Number(raw)
  if (s.over_budget || eta === 0) return t('ringbuffer.migration.keepEtaNow')
  if (eta !== null && Number.isFinite(eta) && eta > 0) {
    return t('ringbuffer.migration.keepEta', { days: fmtInt(Math.max(1, Math.round(eta / SECONDS_PER_DAY))) })
  }
  return null
})

async function onKeep() {
  actionError.value = null
  busy.value = true
  try {
    await decide('keep')
    keepJustChosen.value = true
  } catch (error) {
    actionError.value = extractActionError(error)
  } finally {
    busy.value = false
  }
}

// ── Verwerfen ──────────────────────────────────────────────────────────────
async function onDiscard() {
  actionError.value = null
  busy.value = true
  try {
    await decide('discard')
  } catch (error) {
    actionError.value = extractActionError(error)
  } finally {
    busy.value = false
  }
}
</script>
