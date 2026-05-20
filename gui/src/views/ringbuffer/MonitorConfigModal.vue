<template>
  <Modal v-model="open" title="Monitor konfigurieren" max-width="md">
    <form @submit.prevent="onSubmit" class="flex flex-col gap-4">
      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2" data-testid="rb-config-stats">
        <h4 class="text-sm font-semibold">Ringbuffer Statistik</h4>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>Einträge</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-total">{{ stats?.total ?? '-' }}</span>
        </div>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>Belegter Speicherplatz</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-file-size">{{ formatBytes(stats?.file_size_bytes ?? 0) }}</span>
        </div>
        <div class="text-xs text-slate-500 flex items-center justify-between">
          <span>Effektive Retention (ältester Eintrag)</span>
          <span class="font-medium text-slate-700 dark:text-slate-200" data-testid="rb-config-stats-retention">{{ formatRetention(stats?.effective_retention_seconds ?? null) }}</span>
        </div>
      </div>

      <div class="text-xs text-slate-500">
        Speichermodell ist serverseitig fix: <span class="font-semibold">file-only</span>.
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="max-entries-enabled" type="checkbox" v-model="configForm.maxEntriesEnabled" data-testid="rb-config-max-entries-enabled" />
          <label for="max-entries-enabled" class="text-sm font-medium">Max. Einträge</label>
        </div>
        <input
          v-model.trim="configForm.maxEntriesValue"
          type="number"
          min="100"
          max="1000000"
          step="100"
          class="input"
          :disabled="!configForm.maxEntriesEnabled"
          data-testid="rb-config-max-entries"
          placeholder="z. B. 10000"
        />
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="max-size-enabled" type="checkbox" v-model="configForm.maxSizeEnabled" data-testid="rb-config-max-size-enabled" />
          <label for="max-size-enabled" class="text-sm font-medium">Max. Speicherplatz auf Platte</label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <input
            v-model.trim="configForm.maxSizeValue"
            type="number"
            min="1"
            step="1"
            class="input"
            :disabled="!configForm.maxSizeEnabled"
            data-testid="rb-config-max-size-value"
            placeholder="z. B. 500"
          />
          <select
            v-model="configForm.maxSizeUnit"
            class="input"
            :disabled="!configForm.maxSizeEnabled"
            data-testid="rb-config-max-size-unit"
          >
            <option value="mb">MB</option>
            <option value="gb">GB</option>
          </select>
        </div>
      </div>

      <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <input id="retention-enabled" type="checkbox" v-model="configForm.retentionEnabled" data-testid="rb-config-retention-enabled" />
          <label for="retention-enabled" class="text-sm font-medium">Max. Retention</label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <input
            v-model.trim="configForm.retentionValue"
            type="number"
            min="0"
            step="1"
            class="input"
            :disabled="!configForm.retentionEnabled"
            data-testid="rb-config-retention-value"
            placeholder="z. B. 30"
          />
          <select
            v-model="configForm.retentionUnit"
            class="input"
            :disabled="!configForm.retentionEnabled"
            data-testid="rb-config-retention-unit"
          >
            <option value="days">Tage</option>
            <option value="months">Monate</option>
            <option value="years">Jahre</option>
          </select>
        </div>
      </div>

      <div v-if="configMsg" :class="['p-3 rounded-lg text-sm', configMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">
        {{ configMsg.text }}
      </div>
      <div class="flex justify-end gap-3">
        <button type="button" @click="open = false" class="btn-secondary">Schliessen</button>
        <button type="submit" class="btn-primary" :disabled="saving" data-testid="rb-config-save">
          <Spinner v-if="saving" size="sm" color="white" />
          Speichern
        </button>
      </div>
    </form>
  </Modal>
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
import { ringbufferApi } from '@/api/client'
import { formatDurationDeutsch } from '@/composables/useTimeFilterParser'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const SIZE_UNIT_FACTORS = { mb: 1024 * 1024, gb: 1024 * 1024 * 1024 }
const RETENTION_UNIT_SECONDS = {
  days: 24 * 60 * 60,
  months: 30 * 24 * 60 * 60,
  years: 365 * 24 * 60 * 60,
}

const open = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const stats = ref(null)
const saving = ref(false)
const configMsg = ref(null)
let closeTimer = null
const configForm = reactive({
  maxEntriesEnabled: false,
  maxEntriesValue: '50000',
  maxSizeEnabled: true,
  maxSizeValue: '10',
  maxSizeUnit: 'mb',
  retentionEnabled: false,
  retentionValue: '30',
  retentionUnit: 'days',
})

function formatBytes(rawBytes) {
  const bytes = Number(rawBytes)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes >= SIZE_UNIT_FACTORS.gb) return `${(bytes / SIZE_UNIT_FACTORS.gb).toFixed(2)} GB`
  if (bytes >= SIZE_UNIT_FACTORS.mb) return `${(bytes / SIZE_UNIT_FACTORS.mb).toFixed(2)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(2)} KB`
  return `${Math.round(bytes)} B`
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

function pickRetentionUnit(seconds) {
  if (seconds % RETENTION_UNIT_SECONDS.years === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.years), unit: 'years' }
  if (seconds % RETENTION_UNIT_SECONDS.months === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.months), unit: 'months' }
  if (seconds % RETENTION_UNIT_SECONDS.days === 0) return { value: String(seconds / RETENTION_UNIT_SECONDS.days), unit: 'days' }
  return { value: String(Math.ceil(seconds / RETENTION_UNIT_SECONDS.days)), unit: 'days' }
}

function hydrateForm(currentStats) {
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
}

function buildPayload() {
  const payload = { storage: 'file', max_entries: null, max_file_size_bytes: null, max_age: null }
  if (configForm.maxEntriesEnabled) {
    const maxEntries = parseNonNegativeInteger(configForm.maxEntriesValue)
    if (maxEntries === null || maxEntries < 100) throw new Error('Max. Einträge muss mindestens 100 sein')
    payload.max_entries = maxEntries
  }
  if (configForm.maxSizeEnabled) {
    const sizeValue = parseNonNegativeInteger(configForm.maxSizeValue)
    if (sizeValue === null || sizeValue <= 0) throw new Error('Max. Speicherplatz muss grösser als 0 sein')
    payload.max_file_size_bytes = sizeValue * SIZE_UNIT_FACTORS[configForm.maxSizeUnit]
  }
  if (configForm.retentionEnabled) {
    const retentionValue = parseNonNegativeInteger(configForm.retentionValue)
    if (retentionValue === null) throw new Error('Retention muss eine gültige Zahl sein')
    payload.max_age = retentionValue * RETENTION_UNIT_SECONDS[configForm.retentionUnit]
  }
  return payload
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
  saving.value = true
  configMsg.value = null
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
  try {
    const payload = buildPayload()
    const { data } = await ringbufferApi.config(payload)
    stats.value = data
    hydrateForm(data)
    configMsg.value = { ok: true, text: 'Monitor-Konfiguration gespeichert' }
    closeTimer = setTimeout(() => {
      open.value = false
      configMsg.value = null
      closeTimer = null
    }, 2000)
  } catch (error) {
    configMsg.value = { ok: false, text: error?.response?.data?.detail || error?.message || 'Speichern fehlgeschlagen' }
  } finally {
    saving.value = false
  }
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
