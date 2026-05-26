<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Chart,
  LineController, LineElement, PointElement,
  LinearScale, CategoryScale,
  Filler, Tooltip, Legend,
  BarController, BarElement,
} from 'chart.js'
import { history } from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import type { DataPointValue } from '@/types'
import { TIME_RANGE_PRESETS, DEFAULT_TIME_RANGE, resolveTimeRange } from './timeRangePresets'

Chart.register(
  LineController, LineElement, PointElement,
  LinearScale, CategoryScale,
  Filler, Tooltip, Legend,
  BarController, BarElement,
)

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const ws = useWebSocket()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const chartType = computed<'line' | 'bar'>(() =>
  (props.config.chart_type as string | undefined) === 'bar' ? 'bar' : 'line',
)

function configTimeRange(config: Record<string, unknown>): string {
  if (config.time_range && typeof config.time_range === 'string') return config.time_range as string
  return DEFAULT_TIME_RANGE
}

const selectedTimeRange = ref(configTimeRange(props.config))

// Reset to config default when the configured default changes
watch(() => props.config.time_range, () => {
  selectedTimeRange.value = configTimeRange(props.config)
})

// 'y' = linke Achse, 'y1' = rechte Achse (Chart.js Achsen-IDs)
interface SeriesDef { id: string; label: string; color: string; axis: 'y' | 'y1' }

const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

const canvas      = ref<HTMLCanvasElement | null>(null)
let chart:        Chart | null = null
let wsOff:        (() => void) | null = null
let reloadTimer:  ReturnType<typeof setTimeout> | null = null
const seriesUnits = ref<string[]>([])

function fmtMs(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// Teilt den Zeitraum in gleich breite Buckets; jeder Bucket hat start/mid/end in ms
function buildBuckets(fromMs: number, toMs: number, count: number) {
  const size = (toMs - fromMs) / count
  return Array.from({ length: count }, (_, i) => ({
    start: fromMs + i * size,
    mid:   fromMs + (i + 0.5) * size,
    end:   fromMs + (i + 1) * size,
  }))
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

function initChart() {
  if (!canvas.value) return
  chart?.destroy()

  const isBar = chartType.value === 'bar'

  chart = new Chart(canvas.value, {
    type: chartType.value,
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
            title: (items) => {
              if (chartType.value === 'bar') return items[0]?.label ?? ''
              return items[0]?.parsed.x != null ? fmtMs(items[0].parsed.x) : ''
            },
            label: (ctx) => {
              const v    = ctx.parsed.y
              if (v == null) return ''
              const u    = seriesUnits.value[ctx.datasetIndex] ?? ''
              const name = ctx.dataset.label || ''
              const val  = u ? `${v.toFixed(2)} ${u}` : String(v)
              return name ? `${name}: ${val}` : val
            },
          },
        },
      },
      scales: {
        x: isBar
          ? {
              type:  'category',
              ticks: { color: '#6b7280', maxTicksLimit: 8, maxRotation: 0 },
              grid:  { color: '#1f2937' },
            }
          : {
              type:  'linear',
              ticks: {
                color:         '#6b7280',
                maxTicksLimit: 6,
                maxRotation:   0,
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
}

async function loadData() {
  if (props.editorMode) return

  const defs = buildSeriesDefs()
  if (defs.length === 0 || !chart) return

  const { from: fromDate, to: toDate } = resolveTimeRange(selectedTimeRange.value)

  const results = await Promise.all(
    defs.map(s => history.query(s.id, fromDate.toISOString(), toDate.toISOString())),
  )

  seriesUnits.value = results.map(r => r[0]?.u ?? '')

  const hasMultiple = defs.length > 1
  const hasRight    = defs.some(s => s.axis === 'y1')
  const isBar       = chartType.value === 'bar'

  const leftUnit  = defs.reduce<string>((u, s, i) => u || (s.axis === 'y'  ? (seriesUnits.value[i] ?? '') : ''), '')
  const rightUnit = defs.reduce<string>((u, s, i) => u || (s.axis === 'y1' ? (seriesUnits.value[i] ?? '') : ''), '')

  if (isBar) {
    // Daten in gleich breite Zeitbuckets aggregieren (Durchschnitt je Bucket).
    // Alle Serien teilen dieselben Bucket-Mittelpunkte → korrekte Gruppierung.
    const durationHours = (toDate.getTime() - fromDate.getTime()) / 3_600_000
    const numBuckets    = Math.min(Math.max(Math.round(durationHours * 2), 24), 96)
    const buckets       = buildBuckets(fromDate.getTime(), toDate.getTime(), numBuckets)

    chart.data.labels   = buckets.map(b => fmtMs(b.mid))
    chart.data.datasets = defs.map((s, i) => {
      const raw = results[i]
      const data = buckets.map(b => {
        const pts = raw.filter(d => {
          const ts = new Date(d.ts).getTime()
          return ts >= b.start && ts < b.end
        })
        if (pts.length === 0) return null
        return pts.reduce((sum, p) => sum + Number(p.v), 0) / pts.length
      })
      return {
        yAxisID:         s.axis,
        label:           s.label || (hasMultiple ? `Serie ${i + 1}` : ''),
        data,
        backgroundColor: s.color + 'cc',
        borderWidth:     0,
        borderRadius:    2,
      }
    })
  } else {
    chart.data.labels   = undefined
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

    // X-Achse Bereich setzen (nur bei linearer Skala)
    const xAxis = chart.options.scales?.x as Record<string, unknown> | undefined
    if (xAxis) { xAxis.min = fromDate.getTime(); xAxis.max = toDate.getTime() }
  }

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
  initChart()
  loadData()

  // Auf WS-Nachrichten hören: wenn ein relevanter Datenpunkt eintrifft, wird
  // loadData() nach einer kurzen Wartezeit (2 s, debounced) neu aufgerufen.
  // Dadurch holt der Chart immer saubere, vollständige Daten vom Backend —
  // ohne komplizierte In-Place-Mutation, die bei tension > 0 Artefakte erzeugt.
  wsOff = ws.onMessage((msg) => {
    if (!chart || props.editorMode) return
    if (!msg.id || msg.v === undefined) return
    if (!buildSeriesDefs().some(d => d.id === (msg.id as string))) return
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => { reloadTimer = null; loadData() }, 2_000)
  })
})

watch(() => props.datapointId, loadData)
watch(() => props.config, async (newCfg, oldCfg) => {
  const newType = (newCfg?.chart_type as string | undefined) === 'bar' ? 'bar' : 'line'
  const oldType = (oldCfg?.chart_type as string | undefined) === 'bar' ? 'bar' : 'line'
  if (newType !== oldType) {
    initChart()
  }
  await loadData()
}, { deep: true })
watch(selectedTimeRange, loadData)

onUnmounted(() => {
  wsOff?.()
  if (reloadTimer) { clearTimeout(reloadTimer); reloadTimer = null }
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="flex flex-col h-full p-3">
    <div class="flex items-center justify-between gap-2 mb-1 min-w-0">
      <span class="text-xs text-gray-400 truncate">{{ label }}</span>
      <select
        v-if="!editorMode"
        v-model="selectedTimeRange"
        class="shrink-0 text-xs bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-gray-300 focus:outline-none focus:border-blue-500 cursor-pointer"
        title="Zeitbereich wählen"
      >
        <option v-for="p in TIME_RANGE_PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option>
      </select>
    </div>
    <div class="flex-1 min-h-0">
      <canvas v-if="!editorMode" ref="canvas" />
      <div v-else class="flex items-center justify-center h-full text-gray-600 text-sm">
        Verlaufs-Chart
      </div>
    </div>
  </div>
</template>
