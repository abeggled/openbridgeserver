<script setup lang="ts">
import { computed } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import type { DataPointValue } from '@/types'

interface BarConfig {
  label: string
  dp_id: string
  min: number
  max: number
  decimals: number
  prefix: string
  postfix: string
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
  w?: number
  h?: number
}>()

const dpStore = useDatapointsStore()

const widgetLabel = computed(() => (props.config.label as string | undefined) ?? '')
const bars        = computed<BarConfig[]>(() => (props.config.bars as BarConfig[] | undefined) ?? [])
const colors      = computed<string[]>(() => (props.config.colors as string[] | undefined) ?? ['#22c55e', '#f59e0b', '#ef4444'])
const showValue   = computed(() => (props.config.show_value as boolean | undefined) ?? true)

const gradientCss = computed(() => {
  const c = colors.value
  if (c.length === 0) return '#374151'
  if (c.length === 1) return c[0]
  const stops = c.map((color, i) => `${color} ${(i / (c.length - 1)) * 100}%`).join(', ')
  return `linear-gradient(to right, ${stops})`
})

function getPercent(bar: BarConfig, idx: number): number {
  if (props.editorMode) {
    const n = bars.value.length || 1
    return Math.round(((idx + 1) / (n + 1)) * 100)
  }
  const dp = dpStore.getValue(bar.dp_id)
  if (!dp) return 0
  const v = typeof dp.v === 'number' ? dp.v : parseFloat(String(dp.v))
  if (isNaN(v)) return 0
  const min = bar.min ?? 0
  const max = bar.max ?? 100
  if (max <= min) return 0
  return Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100))
}

function getDisplayValue(bar: BarConfig): string {
  if (props.editorMode) return [bar.prefix, '—', bar.postfix].filter(Boolean).join(' ')
  const dp = dpStore.getValue(bar.dp_id)
  if (!dp) return '…'
  const v = typeof dp.v === 'number' ? dp.v : parseFloat(String(dp.v))
  if (isNaN(v)) return String(dp.v ?? '—')
  const formatted = v.toFixed(bar.decimals ?? 1)
  const unit = bar.postfix || dp.u || ''
  return [bar.prefix, formatted, unit].filter(Boolean).join(' ')
}

// Background-size trick: expand the gradient to span the full track width
// so the color at position X in the fill matches position X in the full track.
function fillStyle(pct: number): Record<string, string> {
  return {
    width: `${pct}%`,
    background: gradientCss.value,
    backgroundSize: pct > 0 ? `${10000 / pct}% 100%` : '100% 100%',
    backgroundPosition: '0 0',
  }
}
</script>

<template>
  <div class="flex flex-col h-full p-2 gap-y-1 select-none overflow-hidden">
    <span
      v-if="widgetLabel"
      class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0"
    >{{ widgetLabel }}</span>

    <!--
      CSS-Grid mit 3 Spalten:
        1. max-content  → Label-Spalte wächst automatisch auf die Breite des längsten Labels;
                          alle Balken starten dadurch bündig auf gleicher Höhe.
        2. minmax(0,1fr)→ Balken füllt den verbleibenden Platz.
        3. max-content  → Wert-Spalte passt sich dem breitesten Wert-String an.
      align-content: space-around verteilt die Zeilen gleichmässig über die verfügbare Höhe.
    -->
    <div
      v-if="bars.length > 0"
      class="flex-1 min-h-0"
      style="display: grid; grid-template-columns: max-content minmax(0, 1fr) max-content; column-gap: 0.5rem; row-gap: 0.25rem; align-content: space-around; align-items: center;"
    >
      <template v-for="(bar, i) in bars" :key="i">
        <!-- Label (immer als Grid-Zelle, damit alle Balken bündig starten) -->
        <span
          class="text-xs text-gray-600 dark:text-gray-400 truncate text-right"
          style="max-width: 8rem"
        >{{ bar.label }}</span>

        <!-- Balken -->
        <div
          class="relative overflow-hidden rounded-sm bg-gray-700 dark:bg-gray-700"
          style="height: 0.875rem"
        >
          <div
            class="absolute inset-y-0 left-0 rounded-sm transition-[width] duration-300"
            :style="fillStyle(getPercent(bar, i))"
            data-testid="bar-fill"
          />
        </div>

        <!-- Wert -->
        <span
          class="text-xs tabular-nums text-gray-800 dark:text-gray-200 text-right whitespace-nowrap"
          data-testid="widget-value"
        >{{ showValue ? getDisplayValue(bar) : '' }}</span>
      </template>
    </div>

    <div
      v-else
      class="flex-1 flex items-center justify-center text-gray-600 dark:text-gray-600 text-xs"
    >
      Keine Balken konfiguriert
    </div>
  </div>
</template>
