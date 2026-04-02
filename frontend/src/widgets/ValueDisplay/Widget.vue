<script setup lang="ts">
import { computed } from 'vue'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const decimals = computed(() => (props.config.decimals as number | undefined) ?? 1)

const displayValue = computed(() => {
  if (props.editorMode) return '—'
  if (props.value === null) return '…'
  const v = props.value.v
  if (typeof v === 'number') return v.toFixed(decimals.value)
  if (typeof v === 'boolean') return v ? 'EIN' : 'AUS'
  return String(v ?? '—')
})

const unit = computed(() => {
  if (props.value?.u) return props.value.u
  return (props.config.unit as string | undefined) ?? ''
})

// Farb-Schwellen auswerten
type Threshold = { value: number; color: 'warning' | 'danger' }
const thresholds = computed<Threshold[]>(
  () => (props.config.color_thresholds as Threshold[] | undefined) ?? []
)

const colorClass = computed(() => {
  if (props.value === null || typeof props.value.v !== 'number') return 'text-gray-900 dark:text-gray-100'
  const v = props.value.v as number
  const active = [...thresholds.value]
    .reverse()
    .find((t) => v >= t.value)
  if (!active) return 'text-gray-900 dark:text-gray-100'
  return active.color === 'danger' ? 'text-red-500 dark:text-red-400' : 'text-yellow-600 dark:text-yellow-400'
})

const quality = computed(() => props.value?.q ?? null)
</script>

<template>
  <div class="flex flex-col justify-between h-full p-3 select-none">
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
    <div class="flex items-baseline gap-1 mt-1">
      <span class="text-2xl font-semibold tabular-nums leading-none" :class="colorClass">
        {{ displayValue }}
      </span>
      <span v-if="unit" class="text-sm text-gray-400 dark:text-gray-400">{{ unit }}</span>
    </div>
    <!-- Quality-Indikator -->
    <div class="flex justify-end mt-1">
      <span
        v-if="quality === 'bad'"
        class="w-2 h-2 rounded-full bg-red-500"
        title="Qualität: schlecht"
      />
      <span
        v-else-if="quality === 'uncertain'"
        class="w-2 h-2 rounded-full bg-yellow-400"
        title="Qualität: unsicher"
      />
    </div>
  </div>
</template>
