<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip } from 'chart.js'
import { history } from '@/api/client'
import type { DataPointValue } from '@/types'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Filler, Tooltip)

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

async function loadData() {
  if (!props.datapointId || props.editorMode) return
  const now = new Date()
  const from = new Date(now.getTime() - hours.value * 3_600_000).toISOString()
  const data = await history.query(props.datapointId, from, now.toISOString())

  const points = data.map((d) => ({ x: new Date(d.ts).getTime(), y: Number(d.v) }))

  if (chart) {
    chart.data.datasets[0].data = points
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
      plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
      scales: {
        x: { type: 'linear', ticks: { color: '#6b7280', maxTicksLimit: 6 }, grid: { color: '#1f2937' } },
        y: { ticks: { color: '#6b7280' }, grid: { color: '#1f2937' } },
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
