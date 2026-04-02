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
  readonly?: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')

// Status-Datenpunkt hat Vorrang für die Anzeige; sonst Haupt-Datenpunkt
const displayValue = computed(() => props.statusValue ?? props.value)

function resolveIsOn(v: DataPointValue | null): boolean {
  if (v === null) return false
  const raw = v.v
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'number') return raw !== 0
  return false
}

// Optimistischer lokaler Status: wird nach dem Schreiben sofort aktualisiert,
// bis ein neuer Wert vom Server eintrifft.
const optimisticValue = ref<boolean | null>(null)

// Sobald ein echter Wert ankommt, optimistischen Wert verwerfen
watch(displayValue, () => {
  optimisticValue.value = null
})

const isOn = computed(() => {
  if (optimisticValue.value !== null) return optimisticValue.value
  return resolveIsOn(displayValue.value)
})

const pending = ref(false)

async function toggle() {
  if (props.editorMode || props.readonly || !props.datapointId || pending.value) return
  const next = !isOn.value
  optimisticValue.value = next
  pending.value = true
  try {
    await datapoints.write(props.datapointId, next)
  } catch {
    // Optimistischen Wert bei Fehler zurücksetzen
    optimisticValue.value = null
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <div
    class="flex flex-col items-center justify-center h-full p-3 gap-2 select-none"
    :class="[editorMode || readonly ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="toggle"
  >
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center">{{ label }}</span>
    <!-- Toggle-Schalter -->
    <button
      class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none"
      :class="isOn ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'"
      :disabled="editorMode || readonly || pending"
      :aria-checked="isOn"
      role="switch"
    >
      <span
        class="absolute top-0.5 left-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform duration-200"
        :class="{ 'translate-x-7': isOn }"
      />
    </button>
    <span class="text-xs font-medium" :class="isOn ? 'text-blue-500 dark:text-blue-400' : 'text-gray-400 dark:text-gray-500'">
      {{ isOn ? 'EIN' : 'AUS' }}
    </span>
  </div>
</template>
