<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { datapoints } from '@/api/client'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const min   = computed(() => (props.config.min  as number | undefined) ?? 0)
const max   = computed(() => (props.config.max  as number | undefined) ?? 100)
const step  = computed(() => (props.config.step as number | undefined) ?? 1)

// Status-Datenpunkt hat Vorrang für die Anzeige
const displayValue = computed(() => props.statusValue ?? props.value)

const unit = computed(() => (props.config.unit as string | undefined) ?? (displayValue.value?.u ?? ''))

/** Wandelt DataPointValue in eine Zahl um – akzeptiert number und string */
function toNumber(v: DataPointValue | null): number {
  if (v === null) return min.value
  const raw = v.v
  if (typeof raw === 'number') return raw
  const parsed = parseFloat(String(raw))
  return isNaN(parsed) ? min.value : parsed
}

const localValue  = ref(toNumber(displayValue.value))
const isDragging  = ref(false)

/**
 * pendingValue: gesendeter Wert, der bis zur KNX-Rückmeldung angezeigt wird.
 * Verhindert den Rücksprung auf den alten Wert zwischen Loslassen und Bestätigung.
 * Wird beim nächsten eingehenden Status-Update (oder nach Timeout) gelöscht.
 */
const pendingValue = ref<number | null>(null)
let pendingTimer: ReturnType<typeof setTimeout> | null = null

function clearPending() {
  pendingValue.value = null
  if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null }
}

/**
 * Anzeigewert (Priorität):
 * 1. Ziehen   → localValue   (sofortige UI-Reaktion)
 * 2. Ausstehend → pendingValue  (optimistisch bis KNX bestätigt)
 * 3. Sonst    → aktueller Status-DP-Wert
 */
const shownValue = computed(() => {
  if (isDragging.value)       return localValue.value
  if (pendingValue.value !== null) return pendingValue.value
  return toNumber(displayValue.value)
})

// Sobald eine Statusmeldung eintrifft: pendingValue verwerfen, localValue synchronisieren
watch(displayValue, (v) => {
  clearPending()
  if (!isDragging.value) {
    localValue.value = toNumber(v)
  }
})

// Sicherheits-Reset: falls pointerup ausserhalb des Elements endet
function onWindowPointerUp() {
  if (isDragging.value) isDragging.value = false
}
onMounted(() => window.addEventListener('pointerup', onWindowPointerUp))
onUnmounted(() => {
  window.removeEventListener('pointerup', onWindowPointerUp)
  clearPending()
})

/** @input → Live-Vorschau während des Ziehens */
function onInput(e: Event) {
  isDragging.value = true
  localValue.value = Number((e.target as HTMLInputElement).value)
}

/** @change → feuert beim Loslassen → Wert optimistisch halten und senden */
function onChange(e: Event) {
  localValue.value = Number((e.target as HTMLInputElement).value)
  isDragging.value = false

  // Optimistisch anzeigen bis Status-Rückmeldung eintrifft (max. 5 s)
  pendingValue.value = localValue.value
  pendingTimer = setTimeout(clearPending, 5000)

  sendValue()
}

async function sendValue() {
  if (props.editorMode || props.readonly || !props.datapointId) return
  try {
    await datapoints.write(props.datapointId, localValue.value)
  } catch {
    clearPending()
  }
}
</script>

<template>
  <div class="flex flex-col justify-between h-full p-3 select-none">
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
    <div class="flex items-baseline gap-1 my-1">
      <span class="text-xl font-semibold tabular-nums text-gray-900 dark:text-gray-100">{{ shownValue }}</span>
      <span v-if="unit" class="text-sm text-gray-400 dark:text-gray-400">{{ unit }}</span>
    </div>
    <input
      type="range"
      :min="min"
      :max="max"
      :step="step"
      :value="shownValue"
      :disabled="editorMode || readonly"
      class="w-full accent-blue-500 cursor-pointer disabled:cursor-default disabled:opacity-50"
      @input="onInput"
      @change="onChange"
    />
    <div class="flex justify-between text-xs text-gray-400 dark:text-gray-500 mt-0.5">
      <span>{{ min }}</span>
      <span>{{ max }}</span>
    </div>
  </div>
</template>
