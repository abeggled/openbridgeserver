<template>
  <Modal v-model="open" :title="$t('ringbuffer.configureTitle')" max-width="md">
    <form @submit.prevent="onSubmit" class="flex flex-col gap-4">
      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <input id="monitor-enabled" type="checkbox" v-model="configForm.enabled" data-testid="rb-config-enabled" />
          <label for="monitor-enabled" class="text-sm font-medium">{{ $t('ringbuffer.monitorEnabled') }}</label>
        </div>
        <p class="text-xs text-slate-500">{{ $t('ringbuffer.monitorEnabledHint') }}</p>
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2" data-testid="rb-config-stats">
        <h4 class="text-sm font-semibold">{{ $t('ringbuffer.statsTitle') }}</h4>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>{{ $t('ringbuffer.status') }}</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-enabled">
            {{ stats?.enabled === false ? $t('ringbuffer.disabledStatus') : $t('ringbuffer.enabledStatus') }}
          </span>
        </div>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>{{ $t('ringbuffer.entries') }}</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-total">{{ stats?.total ?? '-' }}</span>
        </div>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>{{ $t('ringbuffer.statsDiskUsage') }}</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-file-size">{{ formatBytes(stats?.file_size_bytes ?? 0) }}</span>
        </div>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>{{ $t('ringbuffer.statsRetention') }}</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-retention">{{ formatRetention(stats?.effective_retention_seconds ?? null) }}</span>
        </div>
      </div>

      <!-- Prognose (#919/#938): gemeinsame PrognosisBlock-Komponente. -->
      <PrognosisBlock
        :prognosis="stats?.prognosis ?? null"
        :segment-age-hours="segmentAgeHoursForPrognosis"
        :max-file-size-bytes="stats?.max_file_size_bytes ?? null"
      />

      <!-- Retention-Signal (#919/#938): nur bei echter Fehlanpassung (Budget-Boden
           gesprengt = rot, Retention unter Age-Ziel = amber). Normaler Budget-
           Füllstand erzeugt hier bewusst KEIN Signal. -->
      <div
        v-if="retentionSignal.level !== 'none'"
        class="rounded-md border px-3 py-2 text-xs"
        :class="retentionSignal.level === 'error'
          ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
          : 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'"
        data-testid="rb-config-retention-signal"
      >
        {{ retentionSignal.text }}
      </div>

      <div class="text-xs text-slate-500">
        {{ $t('ringbuffer.storageFixed') }} <span class="font-semibold">file-only</span>.
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="max-entries-enabled" type="checkbox" v-model="configForm.maxEntriesEnabled" :disabled="!configForm.enabled" data-testid="rb-config-max-entries-enabled" />
          <label for="max-entries-enabled" class="text-sm font-medium">{{ $t('ringbuffer.maxEntries') }}</label>
        </div>
        <input
          v-model.trim="configForm.maxEntriesValue"
          type="number"
          min="100"
          max="1000000"
          step="100"
          class="input"
          :disabled="!configForm.enabled || !configForm.maxEntriesEnabled"
          data-testid="rb-config-max-entries"
          :placeholder="$t('ringbuffer.maxEntriesPlaceholder')"
        />
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="max-size-enabled" type="checkbox" v-model="configForm.maxSizeEnabled" :disabled="!configForm.enabled" data-testid="rb-config-max-size-enabled" />
          <label for="max-size-enabled" class="text-sm font-medium">{{ $t('ringbuffer.maxDisk') }}</label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <input
            v-model.trim="configForm.maxSizeValue"
            type="number"
            min="1"
            step="1"
            class="input"
            :disabled="!configForm.enabled || !configForm.maxSizeEnabled"
            data-testid="rb-config-max-size-value"
            :placeholder="$t('ringbuffer.maxDiskPlaceholder')"
          />
          <select
            v-model="configForm.maxSizeUnit"
            class="input"
            :disabled="!configForm.enabled || !configForm.maxSizeEnabled"
            data-testid="rb-config-max-size-unit"
          >
            <option value="mb">{{ $t('ringbuffer.unitMiB') }}</option>
            <option value="gb">{{ $t('ringbuffer.unitGiB') }}</option>
          </select>
        </div>
      </div>

      <!-- Effektiver Speicherbedarf (#919): der Budget-Wert ist ein Retention-Ziel,
           kein harter Momentan-Deckel. Zwischen zwei Rotationen wächst das aktive
           Segment oben drauf → kurzzeitiger Sägezahn-Überschwinger. -->
      <p class="text-xs text-slate-500 dark:text-slate-400" data-testid="rb-config-effective-storage-note">
        {{ $t('ringbuffer.effectiveStorageNote') }}
      </p>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="retention-enabled" type="checkbox" v-model="configForm.retentionEnabled" :disabled="!configForm.enabled" data-testid="rb-config-retention-enabled" />
          <label for="retention-enabled" class="text-sm font-medium">{{ $t('ringbuffer.maxRetention') }}</label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <input
            v-model.trim="configForm.retentionValue"
            type="number"
            min="0"
            step="1"
            class="input"
            :disabled="!configForm.enabled || !configForm.retentionEnabled"
            data-testid="rb-config-retention-value"
            :placeholder="$t('ringbuffer.maxRetentionPlaceholder')"
          />
          <select
            v-model="configForm.retentionUnit"
            class="input"
            :disabled="!configForm.enabled || !configForm.retentionEnabled"
            data-testid="rb-config-retention-unit"
          >
            <option value="days">{{ $t('ringbuffer.unitDays') }}</option>
            <option value="months">{{ $t('ringbuffer.unitMonths') }}</option>
            <option value="years">{{ $t('ringbuffer.unitYears') }}</option>
          </select>
        </div>
      </div>

      <!-- Segment-Rotation (#938) — Segmentierung ist automatisch aktiv, es gibt
           KEINEN Aktivierungs-Toggle. Primärparameter: neues Segment alle N
           Stunden. -->
      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3" data-testid="rb-config-segment">
        <div>
          <label for="segment-max-age" class="text-sm font-medium">{{ $t('ringbuffer.segmentMaxAge') }}</label>
          <p class="text-xs text-slate-500 mt-0.5">{{ $t('ringbuffer.segmentMaxAgeHint') }}</p>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <input
            id="segment-max-age"
            v-model.trim="configForm.segmentMaxAgeValue"
            type="number"
            min="1"
            step="1"
            class="input"
            :disabled="!configForm.enabled"
            data-testid="rb-config-segment-max-age"
            :placeholder="$t('ringbuffer.segmentMaxAgePlaceholder')"
          />
          <select
            v-model="configForm.segmentMaxAgeUnit"
            class="input"
            :disabled="!configForm.enabled"
            data-testid="rb-config-segment-max-age-unit"
          >
            <option value="seconds">{{ $t('ringbuffer.unitSeconds') }}</option>
            <option value="minutes">{{ $t('ringbuffer.unitMinutes') }}</option>
            <option value="hours">{{ $t('ringbuffer.unitHours') }}</option>
          </select>
        </div>

        <details class="text-sm">
          <summary class="cursor-pointer text-slate-600 dark:text-slate-300 select-none">{{ $t('ringbuffer.segmentAdvanced') }}</summary>
          <div class="mt-3 flex flex-col gap-3">
            <p class="text-xs text-slate-500">{{ $t('ringbuffer.segmentAdvancedHint') }}</p>
            <div class="flex flex-col gap-1">
              <label for="segment-max-bytes" class="text-xs font-medium text-slate-600 dark:text-slate-300">{{ $t('ringbuffer.segmentMaxBytes') }}</label>
              <div class="grid grid-cols-2 gap-2">
                <input
                  id="segment-max-bytes"
                  v-model.trim="configForm.segmentMaxBytesValue"
                  type="number"
                  min="1"
                  step="1"
                  class="input"
                  :disabled="!configForm.enabled"
                  data-testid="rb-config-segment-max-bytes"
                  :placeholder="$t('ringbuffer.segmentOptionalPlaceholder')"
                />
                <select
                  v-model="configForm.segmentMaxBytesUnit"
                  class="input"
                  :disabled="!configForm.enabled"
                  data-testid="rb-config-segment-max-bytes-unit"
                >
                  <option value="mb">{{ $t('ringbuffer.unitMiB') }}</option>
                  <option value="gb">{{ $t('ringbuffer.unitGiB') }}</option>
                </select>
              </div>
            </div>
            <div class="flex flex-col gap-1">
              <label for="segment-max-rows" class="text-xs font-medium text-slate-600 dark:text-slate-300">{{ $t('ringbuffer.segmentMaxRows') }}</label>
              <input
                id="segment-max-rows"
                v-model.trim="configForm.segmentMaxRowsValue"
                type="number"
                min="1"
                step="1"
                class="input"
                :disabled="!configForm.enabled"
                data-testid="rb-config-segment-max-rows"
                :placeholder="$t('ringbuffer.segmentOptionalPlaceholder')"
              />
            </div>
          </div>
        </details>

        <p class="text-xs text-slate-500">{{ $t('ringbuffer.segmentRatioHint') }}</p>
      </div>

      <div v-if="configMsg" :class="['p-3 rounded-lg text-sm', configMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">
        {{ configMsg.text }}
      </div>
      <div class="flex justify-end gap-3">
        <button type="button" @click="open = false" class="btn-secondary">{{ $t('common.close') }}</button>
        <button type="submit" class="btn-primary" :disabled="saving" data-testid="rb-config-save">
          <Spinner v-if="saving" size="sm" color="white" />
          {{ $t('common.save') }}
        </button>
      </div>
    </form>
  </Modal>
  <ConfirmDialog
    v-model="showDisableConfirm"
    :title="$t('ringbuffer.disableConfirmTitle')"
    :message="$t('ringbuffer.disableConfirmMessage')"
    :confirm-label="$t('ringbuffer.disableConfirmLabel')"
    @confirm="confirmDisable"
  />
</template>

<script setup>
/**
 * MonitorConfigModal — Ringbuffer-Konfigurations-Modal (#438).
 *
 * Extracted from RingBufferView.vue to keep that file lean. Owns the
 * configForm reactive state, fetches /stats on open, and persists changes
 * via ringbufferApi.config.
 *
 * v-model:open controls visibility. On open the modal hydrates its form
 * from the freshly fetched stats. On submit it calls ringbufferApi.config
 * and shows an inline success/error banner.
 */
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ringbufferApi } from '@/api/client'
import { formatDurationDeutsch } from '@/composables/useTimeFilterParser'
import { formatBytesBinary } from '@/utils/formatBytesBinary'
import { useSegmentProblems } from '@/composables/useSegmentProblems'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import Modal from '@/components/ui/Modal.vue'
import PrognosisBlock from '@/components/ringbuffer/PrognosisBlock.vue'
import Spinner from '@/components/ui/Spinner.vue'

const { t } = useI18n()
const { retentionSignal: buildRetentionSignal } = useSegmentProblems()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const SIZE_UNIT_FACTORS = { mb: 1024 * 1024, gb: 1024 * 1024 * 1024 }
const RETENTION_UNIT_SECONDS = {
  days: 24 * 60 * 60,
  months: 30 * 24 * 60 * 60,
  years: 365 * 24 * 60 * 60,
}
// Segmentierung ist automatisch aktiv (#938). Default: neues Segment alle 6 h
// (Backend-Default segment_max_age = 21600 s). /stats liefert den aktuell
// persistierten Wert NICHT zurück, daher hydratisiert das Feld auf den Default.
const DEFAULT_SEGMENT_MAX_AGE_HOURS = 6
const SECONDS_PER_HOUR = 60 * 60
const SECONDS_PER_MINUTE = 60
const SEGMENT_AGE_UNIT_SECONDS = { seconds: 1, minutes: SECONDS_PER_MINUTE, hours: SECONDS_PER_HOUR }
// Backend-Minimum für das Segment-Alter (#938): 300 s (5 min). Sub-Stunden-Werte
// (z. B. aus einem migrierten 15-min-Retention-Fenster) müssen verlustfrei über
// die Minuten-Einheit dargestellt und gespeichert werden – kein Runden auf ganze
// Stunden (Codex #951).
const SEGMENT_MAX_AGE_MIN_SECONDS = 300

const open = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const stats = ref(null)
// Retention-Signal (#919/#938): DRY über useSegmentProblems, hier direkt am
// Config-Formular, wo der Nutzer Budget/Alter anpassen kann.
const retentionSignal = computed(() => buildRetentionSignal(stats.value))
// PrognosisBlock rechnet in Stunden; das Formular kann aber Minuten führen.
// Wert+Einheit → Stunden (auch < 1 h, z. B. 15 min = 0,25 h) umrechnen.
const segmentAgeHoursForPrognosis = computed(() => {
  const value = Number(configForm.segmentMaxAgeValue)
  if (!Number.isFinite(value) || value <= 0) return null
  const seconds = value * SEGMENT_AGE_UNIT_SECONDS[configForm.segmentMaxAgeUnit]
  return seconds / SECONDS_PER_HOUR
})
const saving = ref(false)
const configMsg = ref(null)
// Codex #951: der zuletzt hydratisierte Segment-Alter-Wert (Sekunden) plus das
// hydratisierte Anzeige-Paar (Wert+Einheit). Solange der Nutzer das Alter-Feld
// nicht selbst anfasst, reichen wir diesen Original-Sekundenwert unverändert
// durch – auch < 300 s (migrierter Wert). So scheitert das Speichern einer
// UNRELATED-Einstellung nicht am 300-s-UI-Minimum.
const hydratedSegmentAge = ref(null)
const showDisableConfirm = ref(false)
const pendingDisablePayload = ref(null)
let closeTimer = null
const configForm = reactive({
  enabled: true,
  maxEntriesEnabled: false,
  maxEntriesValue: '50000',
  maxSizeEnabled: true,
  maxSizeValue: '10',
  maxSizeUnit: 'mb',
  retentionEnabled: false,
  retentionValue: '30',
  retentionUnit: 'days',
  // Segment-Rotation (#938). Alter ist der Primärtrigger; Bytes/Rows optional.
  // Wert+Einheit (min/h), damit Sub-Stunden-Alter verlustfrei bleibt (Codex #951).
  segmentMaxAgeValue: String(DEFAULT_SEGMENT_MAX_AGE_HOURS),
  segmentMaxAgeUnit: 'hours',
  segmentMaxBytesValue: '',
  segmentMaxBytesUnit: 'mb',
  segmentMaxRowsValue: '',
})

function formatBytes(rawBytes) {
  return formatBytesBinary(rawBytes)
}

function formatRetention(rawSeconds) {
  const seconds = Number(rawSeconds)
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  return formatDurationDeutsch(seconds)
}

function parseNonNegativeInteger(raw) {
  const parsed = Number.parseInt(String(raw ?? '').trim(), 10)
  if (!Number.isFinite(parsed) || parsed < 0) return null
  return parsed
}

function pickSizeUnit(bytes) {
  if (bytes % SIZE_UNIT_FACTORS.gb === 0) return { value: String(bytes / SIZE_UNIT_FACTORS.gb), unit: 'gb' }
  return { value: String(Math.max(1, Math.round(bytes / SIZE_UNIT_FACTORS.mb))), unit: 'mb' }
}

// Segment-Alter (#938, Codex #951): glatte Stunden über die Stunden-Einheit,
// glatte Minuten über die Minuten-Einheit; alles andere (insb. migrierte
// Sub-300s-Werte wie 200 s aus max_age // 3) verlustfrei über die Sekunden-
// Einheit, statt eine krumme Nachkommazahl in Minuten anzuzeigen oder auf ganze
// Stunden zu runden.
function pickSegmentAgeUnit(seconds) {
  if (seconds % SECONDS_PER_HOUR === 0) return { value: String(seconds / SECONDS_PER_HOUR), unit: 'hours' }
  if (seconds % SECONDS_PER_MINUTE === 0) return { value: String(seconds / SECONDS_PER_MINUTE), unit: 'minutes' }
  return { value: String(seconds), unit: 'seconds' }
}

function pickRetentionUnit(seconds) {
  if (seconds % RETENTION_UNIT_SECONDS.years === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.years), unit: 'years' }
  if (seconds % RETENTION_UNIT_SECONDS.months === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.months), unit: 'months' }
  if (seconds % RETENTION_UNIT_SECONDS.days === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.days), unit: 'days' }
  return { value: String(Math.ceil(seconds / RETENTION_UNIT_SECONDS.days)), unit: 'days' }
}

function hydrateForm(currentStats) {
  configForm.enabled = currentStats?.enabled !== false
  const maxEntries = Number(currentStats?.max_entries)
  if (Number.isFinite(maxEntries) && maxEntries > 0) {
    configForm.maxEntriesEnabled = true
    configForm.maxEntriesValue = String(Math.round(maxEntries))
  } else {
    configForm.maxEntriesEnabled = false
    configForm.maxEntriesValue = '50000'
  }
  const maxFileSize = Number(currentStats?.max_file_size_bytes)
  if (Number.isFinite(maxFileSize) && maxFileSize > 0) {
    const picked = pickSizeUnit(maxFileSize)
    configForm.maxSizeEnabled = true
    configForm.maxSizeValue = picked.value
    configForm.maxSizeUnit = picked.unit
  } else {
    configForm.maxSizeEnabled = false
    configForm.maxSizeValue = '10'
    configForm.maxSizeUnit = 'mb'
  }
  const maxAge = Number(currentStats?.max_age)
  if (Number.isFinite(maxAge) && maxAge > 0) {
    const picked = pickRetentionUnit(maxAge)
    configForm.retentionEnabled = true
    configForm.retentionValue = picked.value
    configForm.retentionUnit = picked.unit
  } else {
    configForm.retentionEnabled = false
    configForm.retentionValue = '30'
    configForm.retentionUnit = 'days'
  }
  // /stats liefert die persistierten Segment-Parameter mit (#919/#938): das Alter
  // wird aus dem gespeicherten Wert (Sekunden) verlustfrei hydratisiert – glatte
  // Stunden über die Stunden-Einheit, Sub-Stunden-Werte (z. B. 900 s = 15 min aus
  // einem migrierten 15-min-Retention-Fenster) über die Minuten-Einheit, statt auf
  // ganze Stunden zu runden (Codex #951). Fehlt der Wert: Backend-Default (6 h).
  const segmentMaxAge = Number(currentStats?.segment_max_age)
  if (Number.isFinite(segmentMaxAge) && segmentMaxAge > 0) {
    const picked = pickSegmentAgeUnit(segmentMaxAge)
    configForm.segmentMaxAgeValue = picked.value
    configForm.segmentMaxAgeUnit = picked.unit
    // Original-Sekundenwert + Anzeige-Paar merken, um ihn beim Speichern eines
    // unangetasteten (ggf. migrierten Sub-300s-) Feldes verlustfrei durchzureichen.
    hydratedSegmentAge.value = { seconds: segmentMaxAge, value: picked.value, unit: picked.unit }
  } else {
    configForm.segmentMaxAgeValue = String(DEFAULT_SEGMENT_MAX_AGE_HOURS)
    configForm.segmentMaxAgeUnit = 'hours'
    hydratedSegmentAge.value = null
  }
  const segmentMaxBytes = Number(currentStats?.segment_max_bytes)
  if (Number.isFinite(segmentMaxBytes) && segmentMaxBytes > 0) {
    const picked = pickSizeUnit(segmentMaxBytes)
    configForm.segmentMaxBytesValue = picked.value
    configForm.segmentMaxBytesUnit = picked.unit
  } else {
    configForm.segmentMaxBytesValue = ''
    configForm.segmentMaxBytesUnit = 'mb'
  }
  const segmentMaxRows = Number(currentStats?.segment_max_rows)
  configForm.segmentMaxRowsValue = Number.isFinite(segmentMaxRows) && segmentMaxRows > 0 ? String(Math.round(segmentMaxRows)) : ''
}

function buildPayload() {
  const payload = { enabled: Boolean(configForm.enabled), storage: 'file' }
  if (!configForm.enabled) return payload

  payload.max_entries = null
  payload.max_file_size_bytes = null
  payload.max_age = null
  if (configForm.maxEntriesEnabled) {
    const maxEntries = parseNonNegativeInteger(configForm.maxEntriesValue)
    if (maxEntries === null || maxEntries < 100) throw new Error(t('ringbuffer.validationMinEntries'))
    payload.max_entries = maxEntries
  }
  if (configForm.maxSizeEnabled) {
    const sizeValue = parseNonNegativeInteger(configForm.maxSizeValue)
    if (sizeValue === null || sizeValue <= 0) throw new Error(t('ringbuffer.validationMinDisk'))
    payload.max_file_size_bytes = sizeValue * SIZE_UNIT_FACTORS[configForm.maxSizeUnit]
  }
  if (configForm.retentionEnabled) {
    const retentionValue = parseNonNegativeInteger(configForm.retentionValue)
    if (retentionValue === null) throw new Error(t('ringbuffer.validationRetentionNaN'))
    payload.max_age = retentionValue * RETENTION_UNIT_SECONDS[configForm.retentionUnit]
  }

  // Segment-Rotation (#938). Alter ist Pflicht (Primärtrigger); Bytes/Rows
  // optional (leer = automatisch vom Backend abgeleitet). Wert+Einheit → Sekunden;
  // das 300-s-Backend-Minimum respektieren und Sub-Stunden-Werte exakt treffen
  // (Codex #951).
  // Hat der Nutzer das Alter-Feld nicht angetastet, den ursprünglich
  // hydratisierten Sekundenwert unverändert durchreichen – auch < 300 s (aus
  // einer migrierten pre-Segmentierungs-Config). Das 300-s-Minimum greift nur,
  // wenn der Nutzer AKTIV einen neuen (zu kleinen) Wert eingibt (Codex #951).
  const pristine = hydratedSegmentAge.value
  const untouched =
    pristine &&
    String(configForm.segmentMaxAgeValue) === pristine.value &&
    configForm.segmentMaxAgeUnit === pristine.unit
  if (untouched) {
    payload.segment_max_age = pristine.seconds
  } else {
    const segmentAgeValue = Number(String(configForm.segmentMaxAgeValue ?? '').trim())
    if (!Number.isFinite(segmentAgeValue) || segmentAgeValue <= 0) throw new Error(t('ringbuffer.validationSegmentMaxAge'))
    const segmentAgeSeconds = Math.round(segmentAgeValue * SEGMENT_AGE_UNIT_SECONDS[configForm.segmentMaxAgeUnit])
    if (segmentAgeSeconds < SEGMENT_MAX_AGE_MIN_SECONDS) throw new Error(t('ringbuffer.validationSegmentMaxAge'))
    payload.segment_max_age = segmentAgeSeconds
  }

  // Leere Segment-Schwelle explizit als null senden (nicht weglassen): der
  // Config-Endpoint behandelt ausgelassene Felder als „persistierten Wert
  // behalten". Ohne explizites null könnte ein zuvor gesetzter Wert nie wieder
  // auf auto zurückgestellt werden (#938, Codex #951).
  if (String(configForm.segmentMaxBytesValue).trim() === '') {
    payload.segment_max_bytes = null
  } else {
    const segmentBytes = parseNonNegativeInteger(configForm.segmentMaxBytesValue)
    if (segmentBytes === null || segmentBytes <= 0) throw new Error(t('ringbuffer.validationSegmentMaxBytes'))
    payload.segment_max_bytes = segmentBytes * SIZE_UNIT_FACTORS[configForm.segmentMaxBytesUnit]
  }
  if (String(configForm.segmentMaxRowsValue).trim() === '') {
    payload.segment_max_rows = null
  } else {
    const segmentRows = parseNonNegativeInteger(configForm.segmentMaxRowsValue)
    if (segmentRows === null || segmentRows <= 0) throw new Error(t('ringbuffer.validationSegmentMaxRows'))
    payload.segment_max_rows = segmentRows
  }
  return payload
}

/**
 * Übersetzt eine Backend-Fehlerantwort in eine anzeigbare Meldung. Der
 * 422-Payload der 3-Segment-Regel liefert ``detail`` als String; FastAPI-
 * Validierungsfehler liefern ``detail`` als Liste ({loc, msg}). Beide Formen
 * werden zu einer lesbaren Zeile verdichtet, statt Rohtext/[object Object].
 */
function extractConfigError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    // NUR den echten 3-Segment-Ratio-Fehler auf die lokalisierte Erklärung
    // abbilden. Das Backend liefert dafür die eindeutige Formulierung
    // „segmentation is too coarse ...“ (obs/ringbuffer/store/config.py,
    // ``_check_ratio``). Frueher wurde jedes ``detail`` mit dem blossen Wort
    // ``segment`` als Ratio-Fehler behandelt – dadurch bekamen explizite
    // Grenzfehler (``segment_max_age ... must be between 300 s ...``)
    // faelschlich die unrelated Ratio-Meldung (#938, Codex #951).
    if (/segmentation is too coarse|zu grob/i.test(detail)) return t('ringbuffer.segmentRatioError')
    // Explizite Grenz-Fehler der Segment-Felder (``must be between ...`` bzw.
    // ``must be >= ...``) tragen die konkrete Einschraenkung bereits im
    // Backend-``detail``; unveraendert durchreichen, damit der Admin die
    // RICHTIGE Grenze sieht statt der Ratio-Meldung.
    return detail
  }
  if (Array.isArray(detail) && detail.length) {
    const msg = detail.map((entry) => entry?.msg).filter(Boolean).join('; ')
    if (msg) return msg
  }
  return error?.message || t('ringbuffer.saveFailed')
}

async function loadStats() {
  try {
    const { data } = await ringbufferApi.stats()
    stats.value = data
    hydrateForm(data)
  } catch {
    // Silent on failure; the modal still renders with the configForm defaults.
  }
}

async function onSubmit() {
  configMsg.value = null
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
  try {
    const payload = buildPayload()
    if (stats.value?.enabled !== false && payload.enabled === false) {
      pendingDisablePayload.value = payload
      showDisableConfirm.value = true
      return
    }
    await savePayload(payload)
  } catch (error) {
    configMsg.value = { ok: false, text: extractConfigError(error) }
  }
}

async function savePayload(payload) {
  saving.value = true
  try {
    const { data } = await ringbufferApi.config(payload)
    stats.value = data
    hydrateForm(data)
    emit('saved', data)
    configMsg.value = { ok: true, text: t('ringbuffer.configSavedModal') }
    closeTimer = setTimeout(() => {
      open.value = false
      configMsg.value = null
      closeTimer = null
    }, 2000)
  } catch (error) {
    configMsg.value = { ok: false, text: extractConfigError(error) }
  } finally {
    saving.value = false
  }
}

async function confirmDisable() {
  const payload = pendingDisablePayload.value
  pendingDisablePayload.value = null
  if (!payload) return
  await savePayload(payload)
}

watch(open, (val) => {
  if (val) {
    configMsg.value = null
    void loadStats()
  } else if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
})

onUnmounted(() => {
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
})
</script>
