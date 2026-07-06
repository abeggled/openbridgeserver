<template>
  <!--
    RingBuffer-/Retention-Karte fürs Admin-Dashboard (#919/#938).

    Lädt ``ringbufferApi.stats()`` beim Mount, refresht per leichtem Intervall
    (30 s) sowie nach Config-Speichern. Drei Zustände:
      1. Monitor deaktiviert  (stats.enabled === false)  → gedämpfter Hinweis
      2. segmentierter Modus   (store != null)           → Budget/Segmente/Retention
      3. Legacy-Modus          (store == null, enabled)   → Basiskennzahlen

    SegmentStatsPanel (Segment-Details) und MonitorConfigModal werden 1:1 aus
    der Monitor-Ansicht wiederverwendet und hier in Modals gehostet.
  -->
  <div class="card flex flex-col" data-testid="dashboard-ringbuffer-card">
    <div class="card-header">
      <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm flex items-center gap-2">
        {{ $t('dashboard.ringbuffer.title') }}
      </h3>
      <RouterLink to="/ringbuffer" class="text-xs text-blue-400 hover:underline">{{ $t('dashboard.ringbuffer.toMonitor') }}</RouterLink>
    </div>

    <div class="card-body flex flex-col gap-4">
      <!-- Migrations-Assistent (#966): kompakter Hinweis, solange die
           Entscheidung zum Legacy-Altbestand aussteht (admin-only, self-gated). -->
      <LegacyMigrationBanner compact @open="showMigrationWizard = true" />

      <!-- Ladezustand -->
      <div v-if="loading" class="flex justify-center py-6" data-testid="rb-card-loading"><Spinner /></div>

      <!-- Fehlerzustand -->
      <div v-else-if="loadError" class="text-center text-slate-500 text-sm py-6" data-testid="rb-card-error">
        {{ $t('dashboard.ringbuffer.loadError') }}
      </div>

      <!-- 1. Monitor deaktiviert -->
      <div v-else-if="disabled" class="flex flex-col items-center gap-3 py-6 text-center opacity-80" data-testid="rb-card-disabled">
        <span class="w-10 h-10 rounded-xl flex items-center justify-center text-xl bg-slate-500/15">🚫</span>
        <div>
          <div class="text-sm font-medium text-slate-600 dark:text-slate-300">{{ $t('dashboard.ringbuffer.disabledTitle') }}</div>
          <p class="text-xs text-slate-500 mt-0.5">{{ $t('dashboard.ringbuffer.disabledHint') }}</p>
        </div>
        <button v-if="auth.isAdmin" type="button" class="btn-secondary" data-testid="rb-card-configure-disabled" @click="showConfig = true">
          {{ $t('dashboard.ringbuffer.configure') }}
        </button>
      </div>

      <!-- 2. Segmentierter Modus -->
      <template v-else-if="segmented">
        <!-- Budget-Auslastung -->
        <div data-testid="rb-card-budget">
          <div class="flex items-center justify-between text-xs mb-1">
            <span class="text-slate-500">{{ $t('dashboard.ringbuffer.budget') }}</span>
            <span class="tabular-nums text-slate-600 dark:text-slate-300" data-testid="rb-card-budget-text">{{ budgetText }}</span>
          </div>
          <!-- FIFO füllt das Budget im Normalbetrieb immer aus → neutral, kein Alarm.
               Echte Fehlanpassung signalisiert der Retention-Block unten (#919/#938). -->
          <div v-if="hasBudget" class="h-2 rounded-full bg-slate-200 dark:bg-slate-700/60 overflow-hidden" data-testid="rb-card-budget-bar">
            <div class="h-full rounded-full bg-blue-500 transition-all" :style="{ width: `${budgetBarWidth}%` }" />
          </div>
          <div v-else class="text-xs text-slate-500" data-testid="rb-card-budget-unlimited">{{ $t('dashboard.ringbuffer.unlimited') }}</div>
          <!-- Super-kurzer Hinweis auf den Sägezahn-Überschwinger (Details im Config-Modal). -->
          <p v-if="hasBudget" class="text-[11px] text-slate-400 mt-1" data-testid="rb-card-budget-peak-hint">{{ $t('dashboard.ringbuffer.budgetPeakHint') }}</p>
        </div>

        <!-- Kennzahlen -->
        <div class="grid grid-cols-2 gap-3">
          <div class="flex flex-col gap-0.5">
            <span class="text-xs text-slate-500">{{ $t('dashboard.ringbuffer.segments') }}</span>
            <span class="text-lg font-bold tabular-nums text-slate-800 dark:text-slate-100" data-testid="rb-card-segments">{{ segmentCount }}</span>
          </div>
        </div>

        <!-- Volle Prognose (#919/#938): gemeinsame PrognosisBlock-Komponente. -->
        <PrognosisBlock
          :prognosis="stats?.prognosis ?? null"
          :segment-age-hours="segmentAgeHours"
          :max-file-size-bytes="stats?.max_file_size_bytes ?? null"
        />

        <!-- Problem-Hinweis (deutlich) -->
        <div
          v-if="problemCount > 0"
          class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300 flex items-center gap-2"
          data-testid="rb-card-problem"
        >
          <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          {{ problemSummary }}
        </div>

        <!-- Retention-Signal (#919/#938): nur bei echter Fehlanpassung, nicht bei
             normalem Budget-Füllstand. error=rot (Budget-Boden gesprengt),
             warn=amber (Retention unter Age-Ziel). -->
        <div
          v-if="retentionSignal.level !== 'none'"
          class="rounded-md border px-3 py-2 text-xs flex items-center gap-2"
          :class="retentionSignal.level === 'error'
            ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
            : 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'"
          data-testid="rb-card-retention-signal"
        >
          <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          {{ retentionSignal.text }}
        </div>

        <!-- Aktionen -->
        <div class="flex flex-wrap gap-2 pt-1">
          <button type="button" class="btn-secondary btn-sm" data-testid="rb-card-open-segments" @click="showSegments = true">
            {{ $t('dashboard.ringbuffer.segmentDetails') }}
          </button>
          <button v-if="auth.isAdmin" type="button" class="btn-secondary btn-sm" data-testid="rb-card-configure" @click="showConfig = true">
            {{ $t('dashboard.ringbuffer.configuration') }}
          </button>
        </div>
      </template>

      <!-- 3. Legacy-Modus -->
      <template v-else>
        <div class="grid grid-cols-2 gap-3" data-testid="rb-card-legacy">
          <div class="flex flex-col gap-0.5">
            <span class="text-xs text-slate-500">{{ $t('dashboard.ringbuffer.entries') }}</span>
            <span class="text-lg font-bold tabular-nums text-slate-800 dark:text-slate-100" data-testid="rb-card-legacy-total">{{ legacyTotal }}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-xs text-slate-500">{{ $t('dashboard.ringbuffer.diskUsage') }}</span>
            <span class="text-lg font-bold tabular-nums text-slate-800 dark:text-slate-100" data-testid="rb-card-legacy-size">{{ legacyFileSize }}</span>
          </div>
        </div>
        <div class="flex flex-wrap gap-2 pt-1">
          <button v-if="auth.isAdmin" type="button" class="btn-secondary btn-sm" data-testid="rb-card-configure" @click="showConfig = true">
            {{ $t('dashboard.ringbuffer.configuration') }}
          </button>
        </div>
      </template>
    </div>

    <!-- Segment-Details: dasselbe Panel wie im Monitor, hier in einem Modal. -->
    <Modal v-model="showSegments" :title="$t('dashboard.ringbuffer.segmentDetails')" max-width="2xl">
      <SegmentStatsPanel v-if="store" :store="store" @open-migration="onOpenMigrationFromSegments" />
    </Modal>

    <!-- Konfiguration: wiederverwendetes MonitorConfigModal. -->
    <MonitorConfigModal v-model="showConfig" @saved="onConfigSaved" />

    <!-- Migrations-Assistent (#966); „Budget prüfen" öffnet das Konfig-Modal. -->
    <LegacyMigrationWizard v-model="showMigrationWizard" @open-config="openConfigFromWizard" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ringbufferApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSegmentProblems } from '@/composables/useSegmentProblems'
import { formatBytesBinary } from '@/utils/formatBytesBinary'
import Spinner from '@/components/ui/Spinner.vue'
import Modal from '@/components/ui/Modal.vue'
import PrognosisBlock from '@/components/ringbuffer/PrognosisBlock.vue'
import LegacyMigrationBanner from '@/components/ringbuffer/LegacyMigrationBanner.vue'
import MonitorConfigModal from '@/views/ringbuffer/MonitorConfigModal.vue'
import SegmentStatsPanel from '@/views/ringbuffer/SegmentStatsPanel.vue'
import LegacyMigrationWizard from '@/views/ringbuffer/LegacyMigrationWizard.vue'

const REFRESH_INTERVAL_MS = 30_000

const { problemCounts, problemSummary: buildProblemSummary, retentionSignal: buildRetentionSignal } = useSegmentProblems()

// Konfig-Aktionen sind admin-only (Backend gibt Nicht-Admins 403). Gleiches
// Gating wie in RingBufferView (#938): Buttons für Nicht-Admins ausblenden.
const auth = useAuthStore()

const stats = ref(null)
const loading = ref(true)
const loadError = ref(false)
const showConfig = ref(false)
const showSegments = ref(false)
const showMigrationWizard = ref(false)
// Wie in RingBufferView: Wizard beim Öffnen des Konfigurators schließen und
// nach dem Schließen der Konfig wieder öffnen (Status wird frisch geladen).
const configOpenedFromWizard = ref(false)
function openConfigFromWizard() {
  configOpenedFromWizard.value = true
  showMigrationWizard.value = false
  showConfig.value = true
}
watch(showConfig, (open) => {
  if (!open && configOpenedFromWizard.value) {
    configOpenedFromWizard.value = false
    showMigrationWizard.value = true
  }
})
let refreshTimer = null

// Einstieg aus dem Segment-Details-Modal (#966): Modal schließen, Assistent öffnen.
function onOpenMigrationFromSegments() {
  showSegments.value = false
  showMigrationWizard.value = true
}

async function load() {
  try {
    const { data } = await ringbufferApi.stats()
    stats.value = data
    loadError.value = false
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function onConfigSaved() {
  await load()
}

onMounted(() => {
  void load()
  refreshTimer = setInterval(() => { void load() }, REFRESH_INTERVAL_MS)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})

const disabled = computed(() => stats.value?.enabled === false)
const store = computed(() => stats.value?.store ?? null)
const segmented = computed(() => !disabled.value && store.value != null)

const common = computed(() => store.value?.common ?? {})
const extra = computed(() => store.value?.backend_extra ?? {})
const segments = computed(() => (Array.isArray(extra.value.segments) ? extra.value.segments : []))

// ── Budget-Auslastung ────────────────────────────────────────────────────
const maxBudgetBytes = computed(() => {
  const n = Number(stats.value?.max_file_size_bytes)
  return Number.isFinite(n) && n > 0 ? n : null
})
const hasBudget = computed(() => maxBudgetBytes.value !== null)
const usedBytes = computed(() => {
  const n = Number(common.value.size_bytes)
  return Number.isFinite(n) && n > 0 ? n : 0
})
const budgetPercent = computed(() => {
  if (!hasBudget.value) return 0
  return (usedBytes.value / maxBudgetBytes.value) * 100
})
const budgetBarWidth = computed(() => Math.min(100, Math.max(0, budgetPercent.value)))
const budgetText = computed(() => {
  if (!hasBudget.value) return formatBytesBinary(usedBytes.value)
  return `${formatBytesBinary(usedBytes.value)} / ${formatBytesBinary(maxBudgetBytes.value)}`
})

// ── Kennzahlen ───────────────────────────────────────────────────────────
function fmtInt(n) {
  const value = Number(n)
  if (!Number.isFinite(value)) return '0'
  try {
    return new Intl.NumberFormat('de-DE').format(value)
  } catch {
    return String(value)
  }
}

const segmentCount = computed(() => fmtInt(common.value.segment_count ?? segments.value.length))

// Segment-Alter (Sekunden → Stunden) für die Budget-Empfehlung der Prognose.
// null → PrognosisBlock lässt die Budget-Zeile weg.
const segmentAgeHours = computed(() => {
  const seconds = Number(stats.value?.segment_max_age)
  return Number.isFinite(seconds) && seconds > 0 ? seconds / 3600 : null
})

// ── Problem-Hinweis ──────────────────────────────────────────────────────
// Kanonische Formulierung wie im Segment-Dialog (#919/#938), keine Duplikate.
const problemCount = computed(() => problemCounts(segments.value).total)
const problemSummary = computed(() => buildProblemSummary(segments.value))
// Retention-Signal aus dem vollen /stats-Objekt (Budget-Boden / Age-Ziel), DRY über useSegmentProblems.
const retentionSignal = computed(() => buildRetentionSignal(stats.value))

// ── Legacy ───────────────────────────────────────────────────────────────
const legacyTotal = computed(() => fmtInt(stats.value?.total ?? 0))
const legacyFileSize = computed(() => formatBytesBinary(stats.value?.file_size_bytes ?? 0))
</script>
