<script setup lang="ts">
import { computed } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import { useFormatStore } from '@/stores/format'
import type { DataPointValue } from '@/types'

interface ExtraDatapoint {
  id: string
  label: string
  unit: string
  decimals: number
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
  h?: number
}>()

const dpStore = useDatapointsStore()
// Anzeige folgt dem konfigurierten Regionalformat (Issue #1073).
const format = useFormatStore()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const decimals = computed(() => (props.config.decimals as number | undefined) ?? 1)
const configUnit = computed(() => (props.config.unit as string | undefined) ?? '')

function formatValue(v: unknown, dec: number): string {
  if (typeof v === 'number') return format.fmtNumber(v, { decimals: dec })
  if (typeof v === 'boolean') return v ? 'EIN' : 'AUS'
  return String(v ?? '—')
}

const displayValue = computed(() => {
  if (props.editorMode) return '—'
  if (props.value === null) return '…'
  return formatValue(props.value.v, decimals.value)
})

const unit = computed(() => props.value?.u ?? configUnit.value)

const quality = computed(() => props.value?.q ?? null)

const extraDatapoints = computed<ExtraDatapoint[]>(
  () => (props.config.extra_datapoints as ExtraDatapoint[] | undefined) ?? []
)

interface ExtraEntry {
  label: string
  value: string
  unit: string
  quality: 'good' | 'bad' | 'uncertain' | null
}

const maxExtra = computed(() => (props.h ?? 3) <= 2 ? 3 : 6)

const extraEntries = computed<ExtraEntry[]>(() => {
  return extraDatapoints.value
    .filter((e) => !!e.id)
    .slice(0, maxExtra.value)
    .map((e) => {
      if (props.editorMode) {
        return { label: e.label || '—', value: '—', unit: e.unit ?? '', quality: null }
      }
      const dpVal = dpStore.getValue(e.id)
      return {
        label: e.label || '—',
        value: dpVal === null ? '…' : formatValue(dpVal.v, e.decimals ?? 1),
        unit: e.unit || dpVal?.u || '',
        quality: dpVal?.q ?? null,
      }
    })
})
</script>

<template>
  <div class="flex flex-col h-full p-3 select-none gap-1">
    <!-- Hauptwert -->
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
    <div class="flex items-baseline gap-1">
      <span class="text-2xl font-semibold tabular-nums leading-none text-gray-900 dark:text-gray-100">
        {{ displayValue }}
      </span>
      <span v-if="unit" class="text-sm text-gray-400 dark:text-gray-400">{{ unit }}</span>
      <span
        v-if="quality === 'bad'"
        class="ml-auto w-2 h-2 rounded-full bg-red-500 flex-shrink-0"
        :title="$t('widgets.common.qualityBad')"
      />
      <span
        v-else-if="quality === 'uncertain'"
        class="ml-auto w-2 h-2 rounded-full bg-yellow-400 flex-shrink-0"
        :title="$t('widgets.common.qualityUncertain')"
      />
    </div>

    <!-- Trennlinie (nur wenn Extra-Werte vorhanden) -->
    <hr v-if="extraEntries.length > 0" class="border-gray-200 dark:border-gray-700 mt-1" />

    <!-- Extra-Werte -->
    <div class="flex flex-col gap-0.5 mt-0.5">
      <div
        v-for="(entry, i) in extraEntries"
        :key="i"
        class="flex items-baseline justify-between gap-1"
      >
        <span class="text-xs text-gray-500 dark:text-gray-400 truncate min-w-0">{{ entry.label }}</span>
        <div class="flex items-baseline gap-0.5 flex-shrink-0">
          <span class="text-sm font-medium tabular-nums text-gray-700 dark:text-gray-300">{{ entry.value }}</span>
          <span v-if="entry.unit" class="text-xs text-gray-400 dark:text-gray-500">{{ entry.unit }}</span>
          <span
            v-if="entry.quality === 'bad'"
            class="w-1.5 h-1.5 rounded-full bg-red-500"
            :title="$t('widgets.common.qualityBad')"
          />
          <span
            v-else-if="entry.quality === 'uncertain'"
            class="w-1.5 h-1.5 rounded-full bg-yellow-400"
            :title="$t('widgets.common.qualityUncertain')"
          />
        </div>
      </div>
    </div>
  </div>
</template>
