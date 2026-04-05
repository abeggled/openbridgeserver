<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { datapoints } from '@/api/client'
import { useDatapointsStore } from '@/stores/datapoints'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const dpStore = useDatapointsStore()

// ── Config ────────────────────────────────────────────────────────────────────
const label     = computed(() => (props.config.label as string) ?? '—')
const mode      = computed(() => (props.config.mode  as string) ?? 'on_off')
const hasDim    = computed(() => ['dimm', 'tw', 'rgb', 'rgbw'].includes(mode.value))
const hasTw     = computed(() => mode.value === 'tw')
const hasColor  = computed(() => ['rgb', 'rgbw'].includes(mode.value))
const hasWhite  = computed(() => mode.value === 'rgbw')

// DP IDs
const dpSwitch       = computed(() => (props.config.dp_switch        as string) || null)
const dpSwitchStatus = computed(() => (props.config.dp_switch_status as string) || null)
const dpDim          = computed(() => (props.config.dp_dim           as string) || null)
const dpDimStatus    = computed(() => (props.config.dp_dim_status    as string) || null)
const dpTw           = computed(() => (props.config.dp_tw            as string) || null)
const dpTwStatus     = computed(() => (props.config.dp_tw_status     as string) || null)
const twWarmK        = computed(() => (props.config.tw_warm_k        as number) ?? 2700)
const twColdK        = computed(() => (props.config.tw_cold_k        as number) ?? 6500)
const dpR            = computed(() => (props.config.dp_r             as string) || null)
const dpG            = computed(() => (props.config.dp_g             as string) || null)
const dpB            = computed(() => (props.config.dp_b             as string) || null)
const dpRStatus      = computed(() => (props.config.dp_r_status      as string) || null)
const dpGStatus      = computed(() => (props.config.dp_g_status      as string) || null)
const dpBStatus      = computed(() => (props.config.dp_b_status      as string) || null)
const dpW            = computed(() => (props.config.dp_w             as string) || null)
const dpWStatus      = computed(() => (props.config.dp_w_status      as string) || null)

// ── Store helpers ─────────────────────────────────────────────────────────────
function getBool(id: string | null): boolean | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v || v.v === null || v.v === undefined) return null
  if (typeof v.v === 'boolean') return v.v
  if (typeof v.v === 'number')  return v.v !== 0
  const s = String(v.v).toLowerCase()
  if (s === 'true'  || s === '1') return true
  if (s === 'false' || s === '0') return false
  return null
}

function getNumber(id: string | null): number | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v) return null
  if (typeof v.v === 'number') return v.v
  const p = parseFloat(String(v.v))
  return isNaN(p) ? null : p
}

async function write(id: string | null, value: unknown) {
  if (!id || props.editorMode || props.readonly) return
  try { await datapoints.write(id, value) } catch { /* ignore */ }
}

// ── Switch ────────────────────────────────────────────────────────────────────
const isOnStore = computed(() => getBool(dpSwitchStatus.value ?? dpSwitch.value))
const isOnOpt   = ref<boolean | null>(null)
watch(isOnStore, () => { isOnOpt.value = null })
const isOn = computed(() => isOnOpt.value ?? isOnStore.value ?? false)

async function toggleSwitch() {
  if (props.editorMode || props.readonly) return
  const next = !isOn.value
  isOnOpt.value = next
  await write(dpSwitch.value, next)
}

// ── Brightness (dim) ──────────────────────────────────────────────────────────
const dimStore = computed(() => getNumber(dpDimStatus.value ?? dpDim.value))
const localDim = ref<number | null>(null)
let dimTimer: ReturnType<typeof setTimeout> | null = null
watch(dimStore, () => { localDim.value = null })
const shownDim = computed(() => localDim.value ?? dimStore.value ?? 0)

function onDimInput(e: Event) {
  localDim.value = Number((e.target as HTMLInputElement).value)
}
async function onDimChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localDim.value = val
  if (dimTimer) clearTimeout(dimTimer)
  dimTimer = setTimeout(() => { localDim.value = null }, 5000)
  await write(dpDim.value, val)
}

// ── Tunable White ─────────────────────────────────────────────────────────────
const twStore = computed(() => getNumber(dpTwStatus.value ?? dpTw.value))
const localTw = ref<number | null>(null)
let twTimer: ReturnType<typeof setTimeout> | null = null
watch(twStore, () => { localTw.value = null })
const shownTw = computed(() => localTw.value ?? twStore.value ?? twWarmK.value)

function onTwInput(e: Event) {
  localTw.value = Number((e.target as HTMLInputElement).value)
}
async function onTwChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localTw.value = val
  if (twTimer) clearTimeout(twTimer)
  twTimer = setTimeout(() => { localTw.value = null }, 5000)
  await write(dpTw.value, val)
}

// ── White channel ─────────────────────────────────────────────────────────────
const wStore = computed(() => getNumber(dpWStatus.value ?? dpW.value))
const localW = ref<number | null>(null)
let wTimer: ReturnType<typeof setTimeout> | null = null
watch(wStore, () => { localW.value = null })
const shownW = computed(() => localW.value ?? wStore.value ?? 0)

function onWInput(e: Event) {
  localW.value = Number((e.target as HTMLInputElement).value)
}
async function onWChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localW.value = val
  if (wTimer) clearTimeout(wTimer)
  wTimer = setTimeout(() => { localW.value = null }, 5000)
  await write(dpW.value, val)
}

// ── Color conversions (per reference documentation) ───────────────────────────
/**
 * RGB (0-255) → HSV
 * Out: H (0-360), S (0-100), V (0-100)
 */
function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  const d = max - min
  let h = 0
  const s = max === 0 ? 0 : d / max
  const v = max
  if (d !== 0) {
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break
      case g: h = ((b - r) / d + 2) / 6; break
      case b: h = ((r - g) / d + 4) / 6; break
    }
  }
  return [Math.round(h * 360), Math.round(s * 100), Math.round(v * 100)]
}

/**
 * HSV → RGB (0-255)
 * In: H (0-360), S (0-100), V (0-100)
 */
function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  h /= 360; s /= 100; v /= 100
  let r = 0, g = 0, b = 0
  const i = Math.floor(h * 6)
  const f = h * 6 - i
  const p = v * (1 - s)
  const q = v * (1 - f * s)
  const t = v * (1 - (1 - f) * s)
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break
    case 1: r = q; g = v; b = p; break
    case 2: r = p; g = v; b = t; break
    case 3: r = p; g = q; b = v; break
    case 4: r = t; g = p; b = v; break
    case 5: r = v; g = p; b = q; break
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)]
}

// ── Color wheel state ─────────────────────────────────────────────────────────
const hue = ref(30)     // H: 0-360
const sat = ref(80)     // S: 0-100

const rStore = computed(() => getNumber(dpRStatus.value ?? dpR.value))
const gStore = computed(() => getNumber(dpGStatus.value ?? dpG.value))
const bStore = computed(() => getNumber(dpBStatus.value ?? dpB.value))

function syncColorFromStore() {
  const r = rStore.value, g = gStore.value, b = bStore.value
  if (r !== null && g !== null && b !== null) {
    const [h, s] = rgbToHsv(r, g, b)
    hue.value = h
    sat.value = s
  }
}

watch([rStore, gStore, bStore], syncColorFromStore)

// Current display color (for lamp icon tint)
const currentRgb = computed(() => hsvToRgb(hue.value, sat.value, 100))
const currentColor = computed(() => {
  if (!hasColor.value) return null
  const [r, g, b] = currentRgb.value
  return `rgb(${r},${g},${b})`
})

// ── Canvas drawing ────────────────────────────────────────────────────────────
const wheelCanvas    = ref<HTMLCanvasElement | null>(null)
const wheelContainer = ref<HTMLElement | null>(null)
const wheelSize      = ref(120)
let resizeObs: ResizeObserver | null = null
let isDragging = false

function drawWheel() {
  const canvas = wheelCanvas.value
  if (!canvas) return
  const size = wheelSize.value
  canvas.width  = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const cx = size / 2, cy = size / 2
  const radius = size / 2 - 2

  // Draw hue-saturation wheel (V = 100 always for selection, brightness separate)
  const imageData = ctx.createImageData(size, size)
  const data = imageData.data
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - cx, dy = y - cy
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist <= radius) {
        const h = ((Math.atan2(dy, dx) / (2 * Math.PI)) * 360 + 360) % 360
        const s = (dist / radius) * 100
        const [r, g, b] = hsvToRgb(h, s, 100)
        const i = (y * size + x) * 4
        data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255
      }
    }
  }
  ctx.putImageData(imageData, 0, 0)

  // Selector dot
  const rad  = (hue.value / 360) * 2 * Math.PI
  const dist = (sat.value / 100) * radius
  const dotX = cx + dist * Math.cos(rad)
  const dotY = cy + dist * Math.sin(rad)
  ctx.beginPath()
  ctx.arc(dotX, dotY, 7, 0, 2 * Math.PI)
  ctx.fillStyle = 'white'
  ctx.fill()
  ctx.beginPath()
  ctx.arc(dotX, dotY, 7, 0, 2 * Math.PI)
  ctx.strokeStyle = 'rgba(0,0,0,0.4)'
  ctx.lineWidth = 1.5
  ctx.stroke()
  // Inner color preview inside the dot
  const [r, g, b] = hsvToRgb(hue.value, sat.value, 100)
  ctx.beginPath()
  ctx.arc(dotX, dotY, 4, 0, 2 * Math.PI)
  ctx.fillStyle = `rgb(${r},${g},${b})`
  ctx.fill()
}

watch([hue, sat], () => nextTick(drawWheel))

// ── Wheel pointer interaction ─────────────────────────────────────────────────
function eventToHueSat(e: PointerEvent): { h: number; s: number } | null {
  const canvas = wheelCanvas.value
  if (!canvas) return null
  const rect   = canvas.getBoundingClientRect()
  const size   = wheelSize.value
  const scale  = size / rect.width
  const x      = (e.clientX - rect.left) * scale
  const y      = (e.clientY - rect.top)  * scale
  const cx = size / 2, cy = size / 2
  const radius = size / 2 - 2
  const dx = x - cx, dy = y - cy
  const dist = Math.min(Math.sqrt(dx * dx + dy * dy), radius)
  const h = ((Math.atan2(dy, dx) / (2 * Math.PI)) * 360 + 360) % 360
  const s = (dist / radius) * 100
  return { h: Math.round(h), s: Math.round(s) }
}

function onWheelPointerDown(e: PointerEvent) {
  if (props.editorMode || props.readonly) return
  isDragging = true
  ;(e.target as HTMLCanvasElement).setPointerCapture(e.pointerId)
  const hs = eventToHueSat(e)
  if (hs) { hue.value = hs.h; sat.value = hs.s }
}

function onWheelPointerMove(e: PointerEvent) {
  if (!isDragging) return
  const hs = eventToHueSat(e)
  if (hs) { hue.value = hs.h; sat.value = hs.s }
}

async function onWheelPointerUp(e: PointerEvent) {
  if (!isDragging) return
  isDragging = false
  const hs = eventToHueSat(e)
  if (hs) { hue.value = hs.h; sat.value = hs.s }
  // Send current color to KNX (V=100; brightness controlled separately via dp_dim)
  const [r, g, b] = hsvToRgb(hue.value, sat.value, 100)
  await Promise.all([
    write(dpR.value, r),
    write(dpG.value, g),
    write(dpB.value, b),
  ])
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────
onMounted(() => {
  syncColorFromStore()
  nextTick(() => {
    if (hasColor.value && wheelContainer.value) {
      resizeObs = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect
          const size = Math.floor(Math.min(width, height))
          if (size > 20) { wheelSize.value = size; nextTick(drawWheel) }
        }
      })
      resizeObs.observe(wheelContainer.value)
      const { width, height } = wheelContainer.value.getBoundingClientRect()
      const size = Math.floor(Math.min(width, height))
      if (size > 20) wheelSize.value = size
      drawWheel()
    }
  })
})

onUnmounted(() => {
  resizeObs?.disconnect()
  if (dimTimer) clearTimeout(dimTimer)
  if (twTimer)  clearTimeout(twTimer)
  if (wTimer)   clearTimeout(wTimer)
})
</script>

<template>
  <div class="flex flex-col h-full p-2 select-none gap-1.5">

    <!-- Label -->
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate leading-none">{{ label }}</span>

    <!-- on_off: centred layout -->
    <div
      v-if="mode === 'on_off'"
      class="flex flex-col flex-1 items-center justify-center gap-2"
    >
      <!-- Lamp icon (Material Icons "lightbulb" filled, 24×24) -->
      <svg
        viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg"
        class="w-8 h-8 transition-all duration-300"
        :class="isOn
          ? (hasColor && currentColor ? '' : 'text-yellow-400')
          : 'text-gray-300 dark:text-gray-600'"
        :style="isOn && hasColor && currentColor
          ? { color: currentColor, filter: `drop-shadow(0 0 5px ${currentColor})` }
          : isOn ? { filter: 'drop-shadow(0 0 5px #fbbf24)' } : {}"
      >
        <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26C17.81 13.47 19 11.38 19 9c0-3.86-3.14-7-7-7z"/>
      </svg>
      <!-- Toggle -->
      <button
        class="relative w-12 h-6 rounded-full transition-colors duration-200 focus:outline-none"
        :class="isOn ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'"
        :disabled="editorMode || readonly || !dpSwitch"
        @click="toggleSwitch"
      >
        <span
          class="absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200"
          :class="{ 'translate-x-6': isOn }"
        />
      </button>
      <span class="text-xs font-medium" :class="isOn ? 'text-blue-500' : 'text-gray-400'">
        {{ isOn ? 'EIN' : 'AUS' }}
      </span>
    </div>

    <!-- dimm / tw / rgb / rgbw: column layout -->
    <div v-else class="flex flex-col flex-1 gap-1.5 min-h-0">

      <!-- Toggle row -->
      <div class="flex items-center gap-2 shrink-0">
        <!-- Lamp icon (Material Icons "lightbulb" filled, 24×24) -->
        <svg
          viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg"
          class="w-5 h-5 shrink-0 transition-all duration-300"
          :class="isOn
            ? (hasColor && currentColor ? '' : 'text-yellow-400')
            : 'text-gray-300 dark:text-gray-600'"
          :style="isOn && hasColor && currentColor
            ? { color: currentColor, filter: `drop-shadow(0 0 4px ${currentColor})` }
            : isOn ? { filter: 'drop-shadow(0 0 4px #fbbf24)' } : {}"
        >
          <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26C17.81 13.47 19 11.38 19 9c0-3.86-3.14-7-7-7z"/>
        </svg>
        <button
          class="relative w-10 h-5 rounded-full transition-colors duration-200 focus:outline-none shrink-0"
          :class="isOn ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'"
          :disabled="editorMode || readonly || !dpSwitch"
          @click="toggleSwitch"
        >
          <span
            class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200"
            :class="{ 'translate-x-5': isOn }"
          />
        </button>
        <span class="text-xs font-medium" :class="isOn ? 'text-blue-500' : 'text-gray-400'">
          {{ isOn ? 'EIN' : 'AUS' }}
        </span>
      </div>

      <!-- Brightness slider -->
      <div class="shrink-0">
        <div class="flex justify-between items-center text-xs mb-1">
          <span class="text-gray-400 dark:text-gray-500">☀</span>
          <span class="tabular-nums text-gray-600 dark:text-gray-400">{{ Math.round(shownDim) }}&thinsp;%</span>
          <span class="text-yellow-400">☀</span>
        </div>
        <input
          type="range" min="0" max="100" step="1"
          :value="shownDim"
          :disabled="editorMode || readonly || !dpDim"
          class="w-full h-2 rounded-full cursor-pointer disabled:cursor-default disabled:opacity-40 slider-brightness"
          @input="onDimInput"
          @change="onDimChange"
        />
      </div>

      <!-- Tunable White slider (Kelvin) -->
      <div v-if="hasTw" class="shrink-0">
        <div class="flex justify-between items-center text-xs mb-1">
          <span class="text-orange-400">{{ twWarmK }}&thinsp;K</span>
          <span class="tabular-nums text-gray-600 dark:text-gray-400">{{ Math.round(shownTw) }}&thinsp;K</span>
          <span class="text-blue-300">{{ twColdK }}&thinsp;K</span>
        </div>
        <input
          type="range"
          :min="twWarmK" :max="twColdK" step="100"
          :value="shownTw"
          :disabled="editorMode || readonly || !dpTw"
          class="w-full h-2 rounded-full cursor-pointer disabled:cursor-default disabled:opacity-40 slider-tw"
          @input="onTwInput"
          @change="onTwChange"
        />
      </div>

      <!-- Color wheel -->
      <div
        v-if="hasColor"
        ref="wheelContainer"
        class="flex-1 flex items-center justify-center min-h-0 overflow-hidden"
      >
        <canvas
          ref="wheelCanvas"
          class="rounded-full touch-none"
          :class="editorMode || readonly ? 'cursor-default' : 'cursor-crosshair'"
          :width="wheelSize"
          :height="wheelSize"
          style="max-width: 100%; max-height: 100%; aspect-ratio: 1 / 1;"
          @pointerdown="onWheelPointerDown"
          @pointermove="onWheelPointerMove"
          @pointerup="onWheelPointerUp"
        />
      </div>

      <!-- White slider (RGBW) -->
      <div v-if="hasWhite" class="shrink-0">
        <div class="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
          <span>Weiss</span>
          <span class="tabular-nums">{{ Math.round(shownW) }}</span>
        </div>
        <input
          type="range" min="0" max="255" step="1"
          :value="shownW"
          :disabled="editorMode || readonly || !dpW"
          class="w-full h-2 rounded-full cursor-pointer disabled:cursor-default disabled:opacity-40 slider-white"
          @input="onWInput"
          @change="onWChange"
        />
      </div>

    </div>
  </div>
</template>

<style scoped>
/* ── Brightness slider: dark → bright yellow ── */
.slider-brightness {
  -webkit-appearance: none;
  appearance: none;
  background: linear-gradient(to right, #1f2937, #fbbf24);
  outline: none;
}
.slider-brightness::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-brightness::-moz-range-thumb {
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-brightness::-webkit-slider-runnable-track { background: transparent; }
.slider-brightness::-moz-range-track { background: transparent; }

/* ── Tunable White slider: warm amber → cool blue ── */
.slider-tw {
  -webkit-appearance: none;
  appearance: none;
  background: linear-gradient(to right, #f97316, #fef9c3, #bfdbfe);
  outline: none;
}
.slider-tw::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-tw::-moz-range-thumb {
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-tw::-webkit-slider-runnable-track { background: transparent; }
.slider-tw::-moz-range-track { background: transparent; }

/* ── White channel slider: dark → white ── */
.slider-white {
  -webkit-appearance: none;
  appearance: none;
  background: linear-gradient(to right, #374151, #ffffff);
  outline: none;
}
.slider-white::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-white::-moz-range-thumb {
  width: 14px; height: 14px;
  border-radius: 50%;
  background: white;
  border: 2px solid #9ca3af;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.slider-white::-webkit-slider-runnable-track { background: transparent; }
.slider-white::-moz-range-track { background: transparent; }
</style>
