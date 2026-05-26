<script setup lang="ts">
import { computed, getCurrentInstance, onMounted, onUnmounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, Filler, Tooltip } from 'chart.js'
import { history } from '@/api/client'
import { useIcons } from '@/composables/useIcons'
import { useDatapointsStore } from '@/stores/datapoints'
import { useWebSocket } from '@/composables/useWebSocket'
import type { DataPointValue } from '@/types'
import { TIME_RANGE_PRESETS, DEFAULT_TIME_RANGE, resolveTimeRange } from '@/widgets/Chart/timeRangePresets'

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip)

type CondFn = 'eq' | 'lt' | 'lte' | 'gt' | 'gte'
type DisplayMode = 'value' | 'history' | 'icon_only' | 'gauge_arc' | 'gauge_circle'

interface Rule {
  fn: CondFn | 'default'
  threshold: string
  icon: string
  color: string
  output_type: 'value' | 'text'
  calculation: string
  prefix: string
  text: string
  decimals: number
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
const { getSvg, isSvgIcon, svgIconName } = useIcons()

const gaugeGradId = `gg-${getCurrentInstance()?.uid ?? Math.random().toString(36).slice(2)}`

// ── Config ─────────────────────────────────────────────────────────────────────

const mode          = computed<DisplayMode>(() => (props.config.mode as DisplayMode | undefined) ?? 'value')
const widgetLabel   = computed(() => (props.config.label as string | undefined) ?? '')
const rules         = computed<Rule[]>(() => (props.config.rules as Rule[] | undefined) ?? [])
const secondaryDpId = computed(() => (props.config.secondary_dp_id as string | undefined) ?? '')
const secLabel      = computed(() => (props.config.secondary_label as string | undefined) ?? '')
const secDecimals   = computed(() => (props.config.secondary_decimals as number | undefined) ?? 1)
const gaugeMin      = computed(() => (props.config.gauge_min as number | undefined) ?? 0)
const gaugeMax      = computed(() => (props.config.gauge_max as number | undefined) ?? 100)
const gaugeColors   = computed<string[]>(() => (props.config.gauge_colors as string[] | undefined) ?? ['#22c55e', '#f59e0b', '#ef4444'])

function configHistoryTimeRange(config: Record<string, unknown>): string {
  if (config.history_time_range && typeof config.history_time_range === 'string') return config.history_time_range as string
  return DEFAULT_TIME_RANGE
}

// Zeitbereich nur für das Modal — wird beim Öffnen auf den Config-Wert zurückgesetzt
const modalTimeRange = ref(configHistoryTimeRange(props.config))

// ── Rule evaluation ────────────────────────────────────────────────────────────

const rawValue = computed(() => props.value?.v ?? null)

function testRule(fn: CondFn | 'default', threshold: string, v: unknown): boolean {
  if (fn === 'default') return true
  const tNum = parseFloat(threshold)
  const vNum = typeof v === 'number' ? v : parseFloat(String(v))
  if (!isNaN(vNum) && !isNaN(tNum)) {
    switch (fn) {
      case 'eq':  return vNum === tNum
      case 'lt':  return vNum < tNum
      case 'lte': return vNum <= tNum
      case 'gt':  return vNum > tNum
      case 'gte': return vNum >= tNum
    }
  }
  return fn === 'eq' && String(v) === threshold
}

const activeRule = computed<Rule | null>(() => {
  const v = rawValue.value
  for (const r of rules.value) {
    if (r.fn === 'default') continue
    if (v !== null && testRule(r.fn, r.threshold, v)) return r
  }
  return rules.value.find(r => r.fn === 'default') ?? null
})

// ── Gauge ──────────────────────────────────────────────────────────────────────

// Arc gauge: center (50,55), radius 42, half-circle M 8 55 A 42 42 0 0 1 92 55
const GAUGE_ARC_R    = 42
const GAUGE_CIRC_R   = 38
const gaugeArcLength  = Math.PI * GAUGE_ARC_R           // ≈ 131.95
const gaugeCircLength = 2 * Math.PI * GAUGE_CIRC_R      // ≈ 238.76

const gaugePercent = computed(() => {
  if (props.editorMode) return 0.5
  const v = rawValue.value
  if (v === null || typeof v !== 'number') return 0
  const min = gaugeMin.value
  const max = gaugeMax.value
  if (max <= min) return 0
  return Math.max(0, Math.min(1, (v - min) / (max - min)))
})

const gaugeArcOffset  = computed(() => gaugeArcLength  * (1 - gaugePercent.value))
const gaugeCircOffset = computed(() => gaugeCircLength * (1 - gaugePercent.value))

const gaugeSingleColor = computed(() => gaugeColors.value.length === 1 ? gaugeColors.value[0] : null)

const gaugeGradientStops = computed(() => {
  const colors = gaugeColors.value
  if (colors.length <= 1) return []
  return colors.map((color, i) => ({
    offset: `${(i / (colors.length - 1)) * 100}%`,
    color,
  }))
})

const gaugeArcStroke = computed(() => gaugeSingleColor.value ?? `url(#${gaugeGradId})`)

function lerpHex(c1: string, c2: string, t: number): string {
  const toRgb = (c: string) => {
    const h = c.replace('#', '')
    const full = h.length === 3 ? h.split('').map(x => x + x).join('') : h
    return [parseInt(full.slice(0, 2), 16), parseInt(full.slice(2, 4), 16), parseInt(full.slice(4, 6), 16)] as const
  }
  const [r1, g1, b1] = toRgb(c1)
  const [r2, g2, b2] = toRgb(c2)
  return `rgb(${Math.round(r1 + (r2 - r1) * t)},${Math.round(g1 + (g2 - g1) * t)},${Math.round(b1 + (b2 - b1) * t)})`
}

function interpolateGradient(colors: string[], t: number): string {
  if (colors.length === 1) return colors[0]
  const s = Math.max(0, Math.min(1, t)) * (colors.length - 1)
  const i = Math.min(Math.floor(s), colors.length - 2)
  return lerpHex(colors[i], colors[i + 1], s - i)
}

const gaugeCircleSegments = computed(() => {
  const colors = gaugeColors.value
  if (colors.length <= 1 || gaugePercent.value <= 0) return []
  const N = 60
  const r = GAUGE_CIRC_R
  const segs: Array<{ d: string; color: string }> = []
  for (let i = 0; i < N; i++) {
    const t0 = i / N
    const t1 = (i + 1) / N
    if (t0 >= gaugePercent.value) break
    const tEnd = Math.min(t1, gaugePercent.value)
    const a0 = (-90 + t0 * 360) * (Math.PI / 180)
    const a1 = (-90 + tEnd * 360) * (Math.PI / 180)
    segs.push({
      d: `M ${(50 + r * Math.cos(a0)).toFixed(3)} ${(50 + r * Math.sin(a0)).toFixed(3)} A ${r} ${r} 0 0 1 ${(50 + r * Math.cos(a1)).toFixed(3)} ${(50 + r * Math.sin(a1)).toFixed(3)}`,
      color: interpolateGradient(colors, (t0 + tEnd) / 2),
    })
  }
  return segs
})

// ── Icon ───────────────────────────────────────────────────────────────────────

const activeIcon  = computed(() => activeRule.value?.icon ?? '')
const activeColor = computed(() => activeRule.value?.color ?? '#6b7280')
const svgContent  = ref('')

watch(
  activeIcon,
  async (icon) => {
    if (!isSvgIcon(icon)) { svgContent.value = ''; return }
    svgContent.value = await getSvg(svgIconName(icon))
  },
  { immediate: true },
)

// Tint the SVG with CSS `color` by funnelling all fill references through currentColor.
// Handles four cases:
//   1. Root <svg> fill attr (replace or add)
//   2. Explicit fill="..." on child elements (except fill="none")
//   3. fill:... inside inline style="" attrs (except fill:none)
//   4. fill:... inside embedded <style> blocks (except fill:none)
const coloredSvg = computed(() => {
  if (!svgContent.value) return ''
  const nonNoneFill = /\bfill\s*:\s*(?!none\b)/g
  return svgContent.value
    // 1. Root <svg>: replace existing non-none fill or add one
    .replace(/<svg\b([^>]*)>/, (_, attrs: string) => {
      const updated = /\bfill=/.test(attrs)
        ? attrs.replace(/\bfill="(?!none\b)[^"]*"/, 'fill="currentColor"')
        : `${attrs} fill="currentColor"`
      return `<svg${updated}>`
    })
    // 2. Explicit fill attributes on child elements
    .replace(/\bfill="(?!none\b)[^"]*"/g, 'fill="currentColor"')
    // 3. fill + stroke inside inline style="" attributes
    .replace(/\bstroke="(?!none\b)[^"]*"/g, 'stroke="currentColor"')
    .replace(/\bstyle="([^"]*)"/g, (_, s: string) =>
      `style="${s
        .replace(nonNoneFill, 'fill:currentColor ')
        .replace(/\bstroke\s*:\s*(?!none\b)[^;"]*/g, 'stroke:currentColor')}"`)
    // 4. fill + stroke inside <style> blocks
    .replace(/(<style[^>]*>)([\s\S]*?)(<\/style>)/g, (_, open, css: string, close) =>
      `${open}${css
        .replace(nonNoneFill, 'fill:currentColor ')
        .replace(/\bstroke\s*:\s*(?!none\b)[^;}\n]*/g, 'stroke:currentColor')}${close}`)
})

// ── Display value ──────────────────────────────────────────────────────────────

function applyCalc(v: number, calc: string): number {
  const expr = `${v} ${calc.trim()}`
  if (!/^[\d.\s+\-*/%()e]+$/i.test(expr)) return v
  try {
    // eslint-disable-next-line no-new-func
    return Number(Function(`"use strict"; return (${expr})`)())
  } catch { return v }
}

interface DisplayParts { prefix: string; value: string; postfix: string }

const mainDisplay = computed<DisplayParts>(() => {
  if (props.editorMode) return { prefix: '', value: '—', postfix: '' }
  if (rawValue.value === null) return { prefix: '', value: '…', postfix: '' }
  const rule = activeRule.value
  if (!rule) {
    const v = rawValue.value
    if (typeof v === 'boolean') return { prefix: '', value: v ? 'EIN' : 'AUS', postfix: '' }
    if (typeof v === 'number') return { prefix: '', value: v.toFixed(1), postfix: props.value?.u ?? '' }
    return { prefix: '', value: String(v ?? '—'), postfix: '' }
  }
  if (rule.output_type === 'text') {
    return { prefix: rule.prefix, value: rule.text || '—', postfix: rule.postfix }
  }
  let v: unknown = rawValue.value
  if (typeof v === 'number' && rule.calculation) v = applyCalc(v, rule.calculation)
  if (typeof v === 'boolean') return { prefix: rule.prefix, value: v ? 'EIN' : 'AUS', postfix: rule.postfix }
  const formatted = typeof v === 'number' ? (v as number).toFixed(rule.decimals ?? 1) : String(v ?? '—')
  return { prefix: rule.prefix, value: formatted, postfix: rule.postfix || (props.value?.u ?? '') }
})

// ── Secondary value ────────────────────────────────────────────────────────────

const secondaryDisplay = computed(() => {
  if (!secondaryDpId.value || props.editorMode) return ''
  const dp = dpStore.getValue(secondaryDpId.value)
  if (dp === null) return '…'
  const v = typeof dp.v === 'number' ? dp.v : parseFloat(String(dp.v))
  const formatted = isNaN(v) ? String(dp.v ?? '—') : v.toFixed(secDecimals.value)
  const unit = dp.u ?? ''
  return [secLabel.value, formatted, unit].filter(Boolean).join('\u202F')
})

// ── History chart ──────────────────────────────────────────────────────────────

const ws = useWebSocket()

const canvasEl      = ref<HTMLCanvasElement | null>(null)
const modalOpen     = ref(false)
const modalCanvasEl = ref<HTMLCanvasElement | null>(null)
let miniChart:    Chart | null = null
let modalChart:   Chart | null = null
let wsOff:        (() => void) | null = null
let reloadTimer:  ReturnType<typeof setTimeout> | null = null
let histUnit = ''

function fmtMs(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function makeDataset(color: string) {
  return {
    data: [] as { x: number; y: number }[],
    borderColor: color,
    backgroundColor: `${color}22`,
    borderWidth: 1.5,
    pointRadius: 0,
    fill: true,
    tension: 0.3,
  }
}

async function fetchPoints(timeRange: string) {
  if (!props.datapointId || props.editorMode) return { pts: [], minMs: 0, maxMs: 0 }
  const { from: fromDate, to: toDate } = resolveTimeRange(timeRange)
  const data = await history.query(props.datapointId, fromDate.toISOString(), toDate.toISOString())
  histUnit = data[0]?.u ?? ''
  return {
    pts:   data.map(d => ({ x: new Date(d.ts).getTime(), y: Number(d.v) })),
    minMs: fromDate.getTime(),
    maxMs: toDate.getTime(),
  }
}

// Mini-Chart: immer den konfigurierten Zeitbereich verwenden
async function updateMiniChart() {
  if (mode.value !== 'history') return
  const { pts, minMs, maxMs } = await fetchPoints(configHistoryTimeRange(props.config))
  if (!miniChart) return
  miniChart.data.datasets[0].data = pts
  const xAxis = miniChart.options.scales?.x as any
  if (xAxis) { xAxis.min = minMs; xAxis.max = maxMs }
  miniChart.update()
}

// Modal-Chart: den im Modal gewählten Zeitbereich verwenden
async function updateModalChart() {
  if (!modalChart || !modalOpen.value) return
  const { pts, minMs, maxMs } = await fetchPoints(modalTimeRange.value)
  modalChart.data.datasets[0].data = pts
  const xAxis = modalChart.options.scales?.x as any
  if (xAxis) { xAxis.min = minMs; xAxis.max = maxMs }
  modalChart.update()
}

onMounted(() => {
  // Auf WS-Nachrichten hören: wenn der eigene Datenpunkt aktualisiert wird,
  // wird updateMiniChart() nach 2 s (debounced) aufgerufen. Das Backend hat
  // den Wert bis dahin sicher gespeichert, sodass der Chart saubere Daten
  // ohne Artefakte lädt.
  wsOff = ws.onMessage((msg) => {
    if (mode.value !== 'history' || props.editorMode) return
    if (!msg.id || msg.v === undefined || msg.id !== props.datapointId) return
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      reloadTimer = null
      updateMiniChart()
      updateModalChart()
    }, 2_000)
  })

  if (mode.value !== 'history' || !canvasEl.value) return
  miniChart = new Chart(canvasEl.value, {
    type: 'line',
    data: { datasets: [makeDataset(activeColor.value)] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { type: 'linear', ticks: { display: false }, grid: { color: '#1f293766' } },
        y: { ticks: { display: false }, grid: { color: '#1f293766' } },
      },
    },
  })
  updateMiniChart()
})

watch(() => props.datapointId, updateMiniChart)
watch(() => props.config.history_time_range, updateMiniChart)
watch(modalTimeRange, updateModalChart)

watch(modalOpen, async (open) => {
  if (!open) { modalChart?.destroy(); modalChart = null; return }
  // Zeitbereich im Modal auf den aktuellen Config-Default zurücksetzen
  modalTimeRange.value = configHistoryTimeRange(props.config)
  await new Promise<void>(r => setTimeout(r, 50))
  if (!modalCanvasEl.value) return
  const { pts, minMs, maxMs } = await fetchPoints(modalTimeRange.value)
  modalChart = new Chart(modalCanvasEl.value, {
    type: 'line',
    data: { datasets: [{ ...makeDataset(activeColor.value), data: pts }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index', intersect: false,
          callbacks: {
            title: (items: any[]) => items[0]?.parsed.x != null ? fmtMs(items[0].parsed.x) : '',
            label: (ctx: any) => histUnit ? `${ctx.parsed.y} ${histUnit}` : String(ctx.parsed.y),
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          min: minMs,
          max: maxMs,
          ticks: { color: '#6b7280', maxTicksLimit: 6, maxRotation: 0, callback: (ms: any) => fmtMs(Number(ms)) },
          grid: { color: '#1f2937' },
        },
        y: { ticks: { color: '#6b7280' }, grid: { color: '#1f2937' } },
      },
    },
  })
})

onUnmounted(() => {
  wsOff?.()
  if (reloadTimer) { clearTimeout(reloadTimer); reloadTimer = null }
  miniChart?.destroy()
  modalChart?.destroy()
})

const quality = computed(() => props.value?.q ?? null)
</script>

<template>
  <!-- ── VALUE MODE ────────────────────────────────────────────────────────── -->
  <div v-if="mode === 'value'" class="flex flex-col items-center h-full p-2 select-none">
    <span v-if="widgetLabel" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ widgetLabel }}</span>

    <!-- Icon: 3 flex shares, square, no circle -->
    <div class="min-h-0 flex items-center justify-center w-full" style="flex: 3; aspect-ratio: 1; max-width: 100%">
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
        :style="{ color: activeColor }"
      >{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        :style="{ color: activeColor }"
        v-html="coloredSvg"
      />
    </div>

    <!-- Value: 2 flex shares, larger and more prominent -->
    <div class="min-h-0 flex flex-col items-center justify-center text-center mt-1" style="flex: 2">
      <div class="flex items-baseline justify-center gap-1 flex-wrap">
        <span v-if="mainDisplay.prefix" class="text-sm text-gray-500 dark:text-gray-400">{{ mainDisplay.prefix }}</span>
        <span class="text-2xl font-semibold tabular-nums leading-none text-gray-900 dark:text-gray-100" data-testid="widget-value">{{ mainDisplay.value }}</span>
        <span v-if="mainDisplay.postfix" class="text-base text-gray-500 dark:text-gray-400">{{ mainDisplay.postfix }}</span>
      </div>
      <span v-if="secondaryDisplay" class="text-xs text-gray-400 dark:text-gray-500 tabular-nums">{{ secondaryDisplay }}</span>
    </div>

    <!-- Quality indicator -->
    <div class="flex justify-end w-full mt-0.5">
      <span v-if="quality === 'bad'" class="w-2 h-2 rounded-full bg-red-500" title="Qualität: schlecht" />
      <span v-else-if="quality === 'uncertain'" class="w-2 h-2 rounded-full bg-yellow-400" title="Qualität: undefiniert" />
    </div>
  </div>

  <!-- ── HISTORY MODE ───────────────────────────────────────────────────────── -->
  <div v-else-if="mode === 'history'" class="flex flex-col items-center h-full p-2 select-none">
    <span v-if="widgetLabel" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ widgetLabel }}</span>

    <!-- Icon: 3 flex shares, no circle -->
    <div class="min-h-0 flex items-center justify-center w-full" style="flex: 3; aspect-ratio: 1; max-width: 100%">
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
        :style="{ color: activeColor }"
      >{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        :style="{ color: activeColor }"
        v-html="coloredSvg"
      />
    </div>

    <!-- Value: fixed theme colors -->
    <div class="shrink-0 text-center my-0.5">
      <span class="text-lg font-semibold tabular-nums text-gray-900 dark:text-gray-100" data-testid="widget-value">
        <template v-if="mainDisplay.prefix">{{ mainDisplay.prefix }}&thinsp;</template>{{ mainDisplay.value }}<template v-if="mainDisplay.postfix">&thinsp;{{ mainDisplay.postfix }}</template>
      </span>
    </div>

    <!-- Chart: 2 flex shares, clickable -->
    <div
      class="w-full min-h-0 cursor-pointer rounded overflow-hidden"
      style="flex: 2"
      :title="editorMode ? '' : 'Klicken für Vollansicht'"
      @click="!editorMode && (modalOpen = true)"
    >
      <canvas v-if="!editorMode" ref="canvasEl" class="w-full h-full" />
      <div v-else class="flex items-center justify-center h-full text-gray-600 text-xs">Verlauf</div>
    </div>
  </div>

  <!-- ── GAUGE ARC (Halbkreis-Gauge) ─────────────────────────────────────── -->
  <div v-else-if="mode === 'gauge_arc'" class="flex flex-col items-center h-full p-2 select-none text-gray-900 dark:text-gray-100">
    <span v-if="widgetLabel" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ widgetLabel }}</span>
    <div class="flex-1 min-h-0 w-full flex items-center justify-center">
      <svg viewBox="0 0 100 65" class="w-full max-h-full" style="overflow: visible">
        <defs>
          <linearGradient :id="gaugeGradId" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop v-for="stop in gaugeGradientStops" :key="stop.offset" :offset="stop.offset" :stop-color="stop.color" />
          </linearGradient>
        </defs>
        <!-- Track -->
        <path d="M 8 55 A 42 42 0 0 1 92 55" fill="none" stroke="#374151" stroke-width="9" stroke-linecap="round" />
        <!-- Fill -->
        <path
          d="M 8 55 A 42 42 0 0 1 92 55"
          fill="none"
          :stroke="gaugeArcStroke"
          stroke-width="9"
          stroke-linecap="round"
          :stroke-dasharray="gaugeArcLength"
          :stroke-dashoffset="gaugeArcOffset"
          data-testid="gauge-arc-fill"
        />
        <!-- Value text -->
        <text x="50" y="49" text-anchor="middle" dominant-baseline="auto" font-size="13" font-weight="600" fill="currentColor" data-testid="widget-value">{{ mainDisplay.value }}</text>
        <text v-if="mainDisplay.postfix" x="50" y="60" text-anchor="middle" font-size="8" fill="#6b7280">{{ mainDisplay.postfix }}</text>
        <!-- Min/Max labels -->
        <text x="8" y="64" text-anchor="middle" font-size="7" fill="#6b7280">{{ gaugeMin }}</text>
        <text x="92" y="64" text-anchor="middle" font-size="7" fill="#6b7280">{{ gaugeMax }}</text>
      </svg>
    </div>
    <div class="flex justify-end w-full mt-0.5">
      <span v-if="quality === 'bad'" class="w-2 h-2 rounded-full bg-red-500" title="Qualität: schlecht" />
      <span v-else-if="quality === 'uncertain'" class="w-2 h-2 rounded-full bg-yellow-400" title="Qualität: undefiniert" />
    </div>
  </div>

  <!-- ── GAUGE CIRCLE (Vollkreis-Gauge) ────────────────────────────────────── -->
  <div v-else-if="mode === 'gauge_circle'" class="flex flex-col items-center h-full p-2 select-none text-gray-900 dark:text-gray-100">
    <span v-if="widgetLabel" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ widgetLabel }}</span>
    <div class="flex-1 min-h-0 w-full flex items-center justify-center">
      <svg viewBox="0 0 100 100" class="max-w-full max-h-full" style="aspect-ratio: 1">
        <!-- Track -->
        <circle cx="50" cy="50" r="38" fill="none" stroke="#374151" stroke-width="9" />
        <!-- Fill: single color -->
        <circle
          v-if="gaugeSingleColor"
          cx="50" cy="50" r="38"
          fill="none"
          :stroke="gaugeSingleColor"
          stroke-width="9"
          stroke-linecap="round"
          :stroke-dasharray="gaugeCircLength"
          :stroke-dashoffset="gaugeCircOffset"
          transform="rotate(-90 50 50)"
          data-testid="gauge-circle-fill"
        />
        <!-- Fill: gradient folgt dem Strich (Bogensegmente mit interpolierter Farbe) -->
        <g v-else data-testid="gauge-circle-fill">
          <path
            v-for="(seg, idx) in gaugeCircleSegments"
            :key="idx"
            :d="seg.d"
            fill="none"
            :stroke="seg.color"
            stroke-width="9"
            stroke-linecap="round"
          />
        </g>
        <!-- Value text -->
        <text x="50" y="48" text-anchor="middle" dominant-baseline="auto" font-size="14" font-weight="600" fill="currentColor" data-testid="widget-value">{{ mainDisplay.value }}</text>
        <text v-if="mainDisplay.postfix" x="50" y="62" text-anchor="middle" font-size="10" fill="#6b7280">{{ mainDisplay.postfix }}</text>
      </svg>
    </div>
    <div class="flex justify-end w-full mt-0.5">
      <span v-if="quality === 'bad'" class="w-2 h-2 rounded-full bg-red-500" title="Qualität: schlecht" />
      <span v-else-if="quality === 'uncertain'" class="w-2 h-2 rounded-full bg-yellow-400" title="Qualität: undefiniert" />
    </div>
  </div>

  <!-- ── ICON ONLY (nur Icon + Beschriftung) ───────────────────────────────── -->
  <div v-else class="flex flex-col items-center h-full p-2 select-none">
    <span v-if="widgetLabel" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ widgetLabel }}</span>

    <!-- Spacer top: 1 share → zentriert das Icon vertikal -->
    <div style="flex: 1" />
    <!-- Icon: 3 flex shares (same proportion as VALUE and HISTORY modes) -->
    <div class="min-h-0 flex items-center justify-center w-full" style="flex: 3; aspect-ratio: 1; max-width: 100%">
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
        :style="{ color: activeColor }"
      >{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        :style="{ color: activeColor }"
        v-html="coloredSvg"
      />
    </div>
    <!-- Spacer bottom: 1 share -->
    <div style="flex: 1" />
    <span class="sr-only" data-testid="widget-value">{{ mainDisplay.value }}</span>
  </div>

  <!-- ── MODAL (history) ───────────────────────────────────────────────────── -->
  <Teleport to="body">
    <div
      v-if="modalOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      @click.self="modalOpen = false"
    >
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-2xl p-4 w-[90vw] max-w-2xl h-[60vh] flex flex-col">
        <div class="flex items-center justify-between mb-3 shrink-0 gap-3">
          <div class="flex items-center gap-2 min-w-0">
            <span
              v-if="activeIcon && !isSvgIcon(activeIcon)"
              class="text-2xl leading-none select-none shrink-0"
              :style="{ color: activeColor }"
            >{{ activeIcon }}</span>
            <span
              v-else-if="coloredSvg"
              class="w-6 h-6 [&>svg]:w-full [&>svg]:h-full shrink-0"
              :style="{ color: activeColor }"
              v-html="coloredSvg"
            />
            <span class="text-sm font-medium text-gray-700 dark:text-gray-200 truncate">{{ widgetLabel || 'Verlauf' }}</span>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <select
              v-model="modalTimeRange"
              class="text-xs bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-blue-500 cursor-pointer"
              title="Zeitbereich wählen"
            >
              <option v-for="p in TIME_RANGE_PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
            <button
              class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none"
              @click="modalOpen = false"
            >✕</button>
          </div>
        </div>
        <div class="flex-1 min-h-0">
          <canvas ref="modalCanvasEl" class="w-full h-full" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
