<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, Filler, Tooltip } from 'chart.js'
import { history } from '@/api/client'
import type { DataPointValue } from '@/types'

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip)

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const hours = computed(() => (props.config.hours as number | undefined) ?? 24)

const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart | null = null
let currentUnit = ''

/** Format Unix-ms as short local date+time label for chart ticks */
function fmtMs(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

async function loadData() {
  if (!props.datapointId || props.editorMode) return
  const now = new Date()
  const from = new Date(now.getTime() - hours.value * 3_600_000).toISOString()
  const data = await history.query(props.datapointId, from, now.toISOString())

  const points = data.map((d) => ({ x: new Date(d.ts).getTime(), y: Number(d.v) }))
  currentUnit = data[0]?.u ?? ''

  if (chart) {
    chart.data.datasets[0].data = points
    // Update Y-axis title with unit if available
    const yAxis = chart.options.scales?.y as any
    if (yAxis) {
      yAxis.title = { display: !!currentUnit, text: currentUnit, color: '#6b7280', font: { size: 11 } }
    }
    chart.update()
  }
}

onMounted(() => {
  if (!canvas.value) return
  chart = new Chart(canvas.value, {
    type: 'line',
    data: {
      datasets: [
        {
          data: [],
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            title: (items) => items[0]?.parsed.x != null ? fmtMs(items[0].parsed.x) : '',
            label: (ctx) => {
              const v = ctx.parsed.y
              return currentUnit ? `${v} ${currentUnit}` : String(v)
            },
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          ticks: {
            color: '#6b7280',
            maxTicksLimit: 6,
            maxRotation: 0,
            callback: (ms) => ms == null ? '' : fmtMs(Number(ms)),
          },
          grid: { color: '#1f2937' },
        },
        y: {
          ticks: { color: '#6b7280' },
          grid: { color: '#1f2937' },
          title: { display: false, text: '', color: '#6b7280', font: { size: 11 } },
        },
      },
    },
  })
  loadData()
})

// Neu laden wenn datapointId oder hours wechselt
watch(() => [props.datapointId, hours.value], loadData)

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
