<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">History</h2>
      <p class="text-sm text-slate-500 mt-0.5">Historische Werte und Aggregationen</p>
    </div>

    <!-- Controls -->
    <div class="card p-4">
      <div class="flex flex-wrap gap-3 items-end">
        <div class="form-group min-w-52 flex-1">
          <label class="label">DataPoint</label>
          <select v-model="selectedDp" class="input">
            <option value="">DataPoint wählen …</option>
            <option v-for="dp in dpStore.items" :key="dp.id" :value="dp.id">{{ dp.name }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Von</label>
          <input v-model="fromTs" type="datetime-local" class="input" />
        </div>
        <div class="form-group">
          <label class="label">Bis</label>
          <input v-model="toTs" type="datetime-local" class="input" />
        </div>
        <div class="form-group">
          <label class="label">Modus</label>
          <select v-model="mode" class="input">
            <option value="raw">Raw</option>
            <option value="aggregate">Aggregiert</option>
          </select>
        </div>
        <div v-if="mode === 'aggregate'" class="form-group">
          <label class="label">Funktion</label>
          <select v-model="aggFn" class="input">
            <option value="avg">Ø Mittelwert</option>
            <option value="min">Min</option>
            <option value="max">Max</option>
            <option value="last">Letzter</option>
          </select>
        </div>
        <div v-if="mode === 'aggregate'" class="form-group">
          <label class="label">Intervall</label>
          <select v-model="aggInterval" class="input">
            <option v-for="iv in intervals" :key="iv.v" :value="iv.v">{{ iv.l }}</option>
          </select>
        </div>
        <button @click="load" class="btn-primary" :disabled="!selectedDp || loading">
          <Spinner v-if="loading" size="sm" color="white" />
          Laden
        </button>
      </div>
    </div>

    <!-- Chart -->
    <div class="card">
      <div class="card-header">
        <span class="text-sm font-semibold text-slate-800 dark:text-slate-100">{{ chartTitle }}</span>
        <span class="text-xs text-slate-500">{{ points.length }} Punkte</span>
      </div>
      <div class="card-body">
        <div v-if="loading" class="flex justify-center py-16"><Spinner size="lg" /></div>
        <div v-else-if="!points.length && selectedDp" class="text-center text-slate-500 text-sm py-16">Keine Daten im gewählten Zeitraum</div>
        <div v-else-if="!selectedDp" class="text-center text-slate-500 text-sm py-16">DataPoint wählen und «Laden» klicken</div>
        <canvas v-else ref="chartCanvas" class="max-h-80" />
      </div>
    </div>

    <!-- Raw table (raw mode only) -->
    <div v-if="mode === 'raw' && points.length" class="card overflow-hidden">
      <div class="card-header"><span class="text-sm font-semibold text-slate-800 dark:text-slate-100">Rohdaten</span></div>
      <div class="table-wrap max-h-64 overflow-y-auto">
        <table class="table">
          <thead><tr><th>Zeitstempel</th><th>Wert</th><th>Quality</th><th>Adapter</th></tr></thead>
          <tbody>
            <tr v-for="(p, i) in points" :key="i">
              <td class="font-mono text-xs text-slate-400">{{ fmtDateTime(p.ts) }}</td>
              <td class="font-mono text-blue-500 dark:text-blue-300">{{ p.value }}</td>
              <td><Badge :variant="p.quality === 'good' ? 'success' : 'warning'" size="xs">{{ p.quality }}</Badge></td>
              <td class="text-slate-500 text-xs">{{ p.adapter_type ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { historyApi } from '@/api/client'
import { useDatapointStore } from '@/stores/datapoints'
import { useTz } from '@/composables/useTz'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend } from 'chart.js'
import 'chart.js/auto'

const { fmtDateTime, fmtChartLabel, toDatetimeLocal, fromDatetimeLocal } = useTz()

const route   = useRoute()
const dpStore = useDatapointStore()

const selectedDp  = ref(route.query.dp ?? '')
const fromTs      = ref(toDatetimeLocal(new Date(Date.now() - 24 * 3600 * 1000)))
const toTs        = ref(toDatetimeLocal(new Date()))
const mode        = ref('aggregate')
const aggFn       = ref('avg')
const aggInterval = ref('1h')
const loading     = ref(false)
const points      = ref([])
const chartCanvas = ref(null)
let   chartInstance = null

const intervals = [
  { v: '1m', l: '1 Minute' }, { v: '5m', l: '5 Minuten' }, { v: '15m', l: '15 Minuten' },
  { v: '30m', l: '30 Minuten' }, { v: '1h', l: '1 Stunde' },
  { v: '6h', l: '6 Stunden' }, { v: '12h', l: '12 Stunden' }, { v: '1d', l: '1 Tag' },
]

const chartTitle = computed(() => {
  if (!selectedDp.value) return 'Verlauf'
  const dp = dpStore.items.find(d => d.id === selectedDp.value)
  return dp ? `${dp.name} ${mode.value === 'aggregate' ? `(${aggFn.value} / ${aggInterval.value})` : '(raw)'}` : 'Verlauf'
})

// defaultFrom is no longer needed — fromTs is initialized via toDatetimeLocal()

onMounted(async () => {
  if (!dpStore.items.length) await dpStore.fetchPage(0, 200)
  if (selectedDp.value) await load()
})

async function load() {
  if (!selectedDp.value) return
  loading.value = true
  points.value  = []
  try {
    const from = fromDatetimeLocal(fromTs.value)
    const to   = fromDatetimeLocal(toTs.value)

    if (mode.value === 'raw') {
      const { data } = await historyApi.query(selectedDp.value, { from, to, limit: 1000 })
      points.value = data
    } else {
      const { data } = await historyApi.aggregate(selectedDp.value, { fn: aggFn.value, interval: aggInterval.value, from, to })
      points.value = data
    }
    await nextTick()
    renderChart()
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartCanvas.value || !points.value.length) return
  chartInstance?.destroy()

  const labels = points.value.map(p => fmtChartLabel(p.ts))
  const values = points.value.map(p => p.value)

  const dark = document.documentElement.classList.contains('dark')
  const tickColor   = dark ? '#64748b' : '#94a3b8'
  const gridColor   = dark ? '#1e2435' : '#f1f5f9'
  const tooltipBg   = dark ? '#1e2435' : '#ffffff'
  const tooltipBody = dark ? '#e2e8f0' : '#1e293b'
  const tooltipTitle = dark ? '#94a3b8' : '#475569'
  const tooltipBorder = dark ? '#334155' : '#e2e8f0'

  chartInstance = new Chart(chartCanvas.value, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: chartTitle.value,
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        borderWidth: 2,
        pointRadius: points.value.length > 200 ? 0 : 3,
        pointHoverRadius: 5,
        fill: true,
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: tooltipBg, titleColor: tooltipTitle, bodyColor: tooltipBody, borderColor: tooltipBorder, borderWidth: 1 },
      },
      scales: {
        x: { ticks: { color: tickColor, maxTicksLimit: 10 }, grid: { color: gridColor } },
        y: { ticks: { color: tickColor }, grid: { color: gridColor } },
      }
    }
  })
}
</script>
