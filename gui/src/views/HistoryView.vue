<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Historie</h2>
      <p class="text-sm text-slate-500 mt-0.5">Historische Werte und Aggregationen</p>
    </div>

    <!-- Controls -->
    <div class="card p-4">
      <div class="flex flex-wrap gap-3 items-end">
        <div class="form-group min-w-64 flex-1">
          <label class="label">Objekt</label>
          <DpCombobox
            v-model="selectedDp"
            :display-name="selectedDpName"
            @select="onDpSelect"
            placeholder="Objekt suchen …"
          />
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
        <div v-else-if="!selectedDp" class="text-center text-slate-500 text-sm py-16">Objekt wählen und «Laden» klicken</div>
        <canvas v-else ref="chartCanvas" class="max-h-80" />
      </div>
    </div>

    <!-- Raw table (raw mode only) -->
    <div v-if="mode === 'raw' && points.length" class="card overflow-hidden">
      <div class="card-header"><span class="text-sm font-semibold text-slate-800 dark:text-slate-100">Rohdaten</span></div>
      <div class="table-wrap max-h-64 overflow-y-auto">
        <table class="table">
          <thead><tr><th>Zeitstempel</th><th>Wert</th><th>Qualität</th><th>Adapter</th></tr></thead>
          <tbody>
            <tr v-for="(p, i) in points" :key="i">
              <td class="font-mono text-xs text-slate-400">{{ fmtDateTime(p.ts) }}</td>
              <td class="font-mono text-blue-500 dark:text-blue-300">{{ p.v ?? '—' }}<span v-if="p.u" class="text-slate-500 ml-1 text-xs">{{ p.u }}</span></td>
              <td><Badge :variant="p.q === 'good' ? 'success' : 'warning'" size="xs">{{ qualityLabel(p.q) }}</Badge></td>
              <td class="text-slate-500 text-xs">{{ p.a ?? '—' }}</td>
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
import { historyApi, dpApi } from '@/api/client'
import { useTz } from '@/composables/useTz'
import Badge       from '@/components/ui/Badge.vue'
import Spinner     from '@/components/ui/Spinner.vue'
import DpCombobox  from '@/components/ui/DpCombobox.vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend } from 'chart.js'
import 'chart.js/auto'

const { fmtDateTime, fmtChartLabel, toDatetimeLocal, fromDatetimeLocal, toUtcDate } = useTz()

const route = useRoute()

const selectedDp     = ref(route.query.dp ?? '')
const selectedDpName = ref('')
const selectedDpUnit = ref('')

function onDpSelect(dp) {
  if (dp) {
    selectedDp.value     = dp.id
    selectedDpName.value = dp.name
    selectedDpUnit.value = dp.unit ?? ''
  } else {
    selectedDp.value     = ''
    selectedDpName.value = ''
    selectedDpUnit.value = ''
  }
}
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
  const name = selectedDpName.value || selectedDp.value
  return `${name} ${mode.value === 'aggregate' ? `(${aggFn.value} / ${aggInterval.value})` : '(raw)'}`
})

// defaultFrom is no longer needed — fromTs is initialized via toDatetimeLocal()

onMounted(async () => {
  // If opened with ?dp=<uuid>, resolve the name so the combobox shows it
  if (selectedDp.value) {
    try {
      const { data } = await dpApi.get(selectedDp.value)
      selectedDpName.value = data.name
      selectedDpUnit.value = data.unit ?? ''
    } catch { /* ignore */ }
    await load()
  }
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

function qualityLabel(q) {
  return q === 'good' ? 'gut' : q === 'bad' ? 'schlecht' : q === 'uncertain' ? 'undefiniert' : q
}

function renderChart() {
  if (!chartCanvas.value || !points.value.length) return
  chartInstance?.destroy()

  // Convert every point to {x: Unix-ms, y: value} so Chart.js never has to
  // guess the scale type from label strings (which caused it to auto-activate
  // the TimeScale without an adapter and display raw ms).
  const chartData = points.value.map(p => {
    const isoStr = p.ts ?? p.bucket ?? null
    const ms = isoStr ? (toUtcDate(isoStr)?.getTime() ?? 0) : 0
    return { x: ms, y: p.v }
  })

  const dark = document.documentElement.classList.contains('dark')
  const tickColor    = dark ? '#64748b' : '#94a3b8'
  const gridColor    = dark ? '#1e2435' : '#f1f5f9'
  const tooltipBg    = dark ? '#1e2435' : '#ffffff'
  const tooltipBody  = dark ? '#e2e8f0' : '#1e293b'
  const tooltipTitle = dark ? '#94a3b8' : '#475569'
  const tooltipBorder = dark ? '#334155' : '#e2e8f0'

  chartInstance = new Chart(chartCanvas.value, {
    type: 'line',
    data: {
      datasets: [{
        label: chartTitle.value,
        data: chartData,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        borderWidth: 2,
        pointRadius: chartData.length > 200 ? 0 : 3,
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
        tooltip: {
          backgroundColor: tooltipBg, titleColor: tooltipTitle,
          bodyColor: tooltipBody, borderColor: tooltipBorder, borderWidth: 1,
          callbacks: {
            // x-axis label in tooltip: format ms as local time
            title: (items) => fmtChartLabel(new Date(items[0].parsed.x).toISOString()),
            label: (ctx) => {
              const v = ctx.parsed.y
              const unit = mode.value === 'raw'
                ? (points.value[ctx.dataIndex]?.u ?? selectedDpUnit.value)
                : selectedDpUnit.value
              return unit ? `${v} ${unit}` : String(v)
            },
          },
        },
      },
      scales: {
        // linear scale on Unix-ms with a formatting callback — no date adapter needed
        x: {
          type: 'linear',
          ticks: {
            color: tickColor,
            maxTicksLimit: 8,
            maxRotation: 0,
            callback: (ms) => fmtChartLabel(new Date(ms).toISOString()),
          },
          grid: { color: gridColor },
        },
        y: { ticks: { color: tickColor }, grid: { color: gridColor } },
      }
    }
  })
}
</script>
