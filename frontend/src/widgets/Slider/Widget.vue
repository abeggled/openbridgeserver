<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { datapoints } from '@/api/client'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const min = computed(() => (props.config.min as number | undefined) ?? 0)
const max = computed(() => (props.config.max as number | undefined) ?? 100)
const step = computed(() => (props.config.step as number | undefined) ?? 1)

// Status-Datenpunkt hat Vorrang für die Anzeige
const displayValue = computed(() => props.statusValue ?? props.value)

const unit = computed(() => (props.config.unit as string | undefined) ?? (displayValue.value?.u ?? ''))

function resolveNumber(v: DataPointValue | null): number {
  if (v === null) return min.value
  const raw = v.v
  return typeof raw === 'number' ? raw : min.value
}

const localValue = ref(resolveNumber(displayValue.value))
let dragging = false

// Eingehende Werte synchronisieren, solange der Nutzer nicht zieht
watch(displayValue, (v) => {
  if (!dragging) {
    localValue.value = resolveNumber(v)
  }
})

let debounce: ReturnType<typeof setTimeout> | null = null

function onInput(e: Event) {
  dragging = true
  localValue.value = Number((e.target as HTMLInputElement).value)
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => {
    dragging = false
    sendValue()
  }, 300)
}

async function sendValue() {
  if (props.editorMode || !props.datapointId) return
  try {
    await datapoints.write(props.datapointId, localValue.value)
  } catch {
    // Fehler ignorieren — Nutzer sieht den Slider-Wert noch
  }
}
</script>

<template>
  <div class="flex flex-col justify-between h-full p-3 select-none">
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
    <div class="flex items-baseline gap-1 my-1">
      <span class="text-xl font-semibold tabular-nums text-gray-900 dark:text-gray-100">{{ localValue }}</span>
      <span v-if="unit" class="text-sm text-gray-400 dark:text-gray-400">{{ unit }}</span>
    </div>
    <input
      type="range"
      :min="min"
      :max="max"
      :step="step"
      :value="localValue"
      :disabled="editorMode"
      class="w-full accent-blue-500 cursor-pointer"
      @input="onInput"
    />
    <div class="flex justify-between text-xs text-gray-400 dark:text-gray-500 mt-0.5">
      <span>{{ min }}</span>
      <span>{{ max }}</span>
    </div>
  </div>
</template>
