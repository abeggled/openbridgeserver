<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, Filler, Tooltip, Legend } from 'chart.js'
import { history } from '@/api/client'
import type { DataPointValue } from '@/types'

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip, Legend)

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const hours = computed(() => (props.config.hours as number | undefined) ?? 24)

// 'y' = linke Achse, 'y1' = rechte Achse (Chart.js Achsen-IDs)
interface SeriesDef { id: string; label: string; color: string; axis: 'y' | 'y1' }

const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart | null = null
const seriesUnits = ref<string[]>([])

function fmtMs(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function buildSeriesDefs(): SeriesDef[] {
  const result: SeriesDef[] = []

  const primaryColor = (props.config.primary_color as string | undefined) ?? COLORS[0]
  const primaryAxis  = (props.config.primary_axis  as string | undefined) === 'right' ? 'y1' : 'y'

  if (props.datapointId) {
    result.push({ id: props.datapointId, label: label.value, color: primaryColor, axis: primaryAxis })
  }

  const extra = (props.config.series as Array<{
    dp_id?: string; label?: string; color?: string; axis?: string
  }> | undefined) ?? []

  for (const s of extra) {
    if (!s.dp_id) continue
    result.push({
      id:    s.dp_id,
      label: s.label ?? '',
      color: s.color ?? COLORS[result.length % COLORS.length],
      axis:  s.axis === 'right' ? 'y1' : 'y',
    })
  }

  return result
}

async function loadData() {
  if (props.editorMode) return

  const defs = buildSeriesDefs()
  if (defs.length === 0 || !chart) return

  const now      = new Date()
  const fromDate = new Date(now.getTime() - hours.value * 3_600_000)

  const results = await Promise.all(
    defs.map(s => history.query(s.id, fromDate.toISOString(), now.toISOString())),
  )

  seriesUnits.value = results.map(r => r[0]?.u ?? '')

  const hasMultiple = defs.length > 1
  const hasRight    = defs.some(s => s.axis === 'y1')

  // Erste Einheit je Achse für den Achsentitel
  const leftUnit  = defs.reduce<string>((u, s, i) => u || (s.axis === 'y'  ? (seriesUnits.value[i] ?? '') : ''), '')
  const rightUnit = defs.reduce<string>((u, s, i) => u || (s.axis === 'y1' ? (seriesUnits.value[i] ?? '') : ''), '')

  chart.data.datasets = defs.map((s, i) => ({
    yAxisID:         s.axis,
    label:           s.label || (hasMultiple ? `Serie ${i + 1}` : ''),
    data:            results[i].map(d => ({ x: new Date(d.ts).getTime(), y: Number(d.v) })),
    borderColor:     s.color,
    backgroundColor: s.color + '1a',
    borderWidth:     1.5,
    pointRadius:     0,
    fill:            !hasMultiple,
    tension:         0.3,
  }))

  // X-Achse
  const xAxis = chart.options.scales?.x as Record<string, unknown> | undefined
  if (xAxis) { xAxis.min = fromDate.getTime(); xAxis.max = now.getTime() }

  // Linke Y-Achse
  const yLeft = chart.options.scales?.y as Record<string, unknown> | undefined
  if (yLeft) {
    yLeft.title = { display: !!leftUnit, text: leftUnit, color: '#6b7280', font: { size: 11 } }
  }

  // Rechte Y-Achse — nur anzeigen wenn mindestens eine Reihe zugewiesen
  const yRight = chart.options.scales?.y1 as Record<string, unknown> | undefined
  if (yRight) {
    yRight.display = hasRight
    yRight.title   = { display: !!rightUnit && hasRight, text: rightUnit, color: '#6b7280', font: { size: 11 } }
  }

  // Legende
  if (chart.options.plugins) {
    (chart.options.plugins as Record<string, unknown>).legend = {
      display: hasMultiple,
      labels:  { color: '#9ca3af', boxWidth: 12, font: { size: 11 } },
    }
  }

  chart.update()
}

onMounted(() => {
  if (!canvas.value) return
  chart = new Chart(canvas.value, {
    type: 'line',
    data: { datasets: [] },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode:      'index',
          intersect: false,
          callbacks: {
            title: (items) => items[0]?.parsed.x != null ? fmtMs(items[0].parsed.x) : '',
            label: (ctx) => {
              const v    = ctx.parsed.y
              const u    = seriesUnits.value[ctx.datasetIndex] ?? ''
              const name = ctx.dataset.label || ''
              const val  = u ? `${v} ${u}` : String(v)
              return name ? `${name}: ${val}` : val
            },
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          ticks: {
            color:          '#6b7280',
            maxTicksLimit:  6,
            maxRotation:    0,
            callback: (ms) => ms == null ? '' : fmtMs(Number(ms)),
          },
          grid: { color: '#1f2937' },
        },
        y: {
          type:     'linear',
          position: 'left',
          ticks:    { color: '#6b7280' },
          grid:     { color: '#1f2937' },
          title:    { display: false, text: '', color: '#6b7280', font: { size: 11 } },
        },
        y1: {
          type:     'linear',
          position: 'right',
          display:  false,
          ticks:    { color: '#6b7280' },
          grid:     { drawOnChartArea: false, color: '#1f2937' },
          title:    { display: false, text: '', color: '#6b7280', font: { size: 11 } },
        },
      },
    },
  })
  loadData()
})

watch(() => props.datapointId, loadData)
watch(() => props.config, loadData, { deep: true })

onUnmounted(() => {
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="flex flex-col h-full p-3">
    <span class="text-xs text-gray-400 mb-1 truncate">{{ label }}</span>
    <div class="flex-1 min-h-0">
      <canvas v-if="!editorMode" ref="canvas" />
      <div v-else class="flex items-center justify-center h-full text-gray-600 text-sm">
        Verlaufs-Chart
      </div>
    </div>
  </div>
</template>
