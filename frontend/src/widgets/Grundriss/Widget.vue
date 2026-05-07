<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDatapointsStore } from '@/stores/datapoints'
import { WidgetRegistry } from '@/widgets/registry'
import type { DataPointValue } from '@/types'

interface GrundrissArea {
  id: string
  name: string
  points: Array<[number, number]>
  showLabel: boolean
  labelX: number
  labelY: number
  labelColor: string
  actionType: 'none' | 'navigate'
  actionValue: string
}

interface GrundrissMiniWidget {
  id: string
  label: string
  widgetType: string
  config: Record<string, unknown>
  datapointId: string | null
  statusDatapointId: string | null
  x: number        // center in image natural pixels
  y: number        // center in image natural pixels
  wPx: number      // screen width in px
  hPx: number      // screen height in px
  visible: boolean
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const router  = useRouter()
const dpStore = useDatapointsStore()

const image         = computed(() => (props.config.image         as string | null) ?? null)
const naturalW      = computed(() => (props.config.imageNaturalW as number) || 1920)
const naturalH      = computed(() => (props.config.imageNaturalH as number) || 1080)
const rotation      = computed(() => (props.config.rotation      as number) ?? 0)
const showAreaNames = computed(() => (props.config.showAreaNames as boolean) ?? true)
const areas         = computed(() => (props.config.areas         as GrundrissArea[]) ?? [])
const miniWidgets   = computed(() => (props.config.miniWidgets   as GrundrissMiniWidget[]) ?? [])

// ── Container size tracking ───────────────────────────────────────────────────

const wrapperRef = ref<HTMLElement>()
const wrapperW   = ref(300)
const wrapperH   = ref(200)

let ro: ResizeObserver | null = null

onMounted(() => {
  ro = new ResizeObserver(([entry]) => {
    wrapperW.value = entry.contentRect.width
    wrapperH.value = entry.contentRect.height
  })
  if (wrapperRef.value) ro.observe(wrapperRef.value)
})

onUnmounted(() => ro?.disconnect())

// ── Rotated inner container style ─────────────────────────────────────────────
// For 90°/270°: swap the effective width/height so the rotated content fills the container.
// The inner div is sized H×W (swapped), offset so its centre matches the wrapper centre,
// then CSS-rotated so it appears W×H — matching the wrapper exactly.

const innerStyle = computed(() => {
  const r = rotation.value
  const W = wrapperW.value
  const H = wrapperH.value
  if (r === 90 || r === 270) {
    return {
      position: 'absolute' as const,
      width:     `${H}px`,
      height:    `${W}px`,
      top:       `${(H - W) / 2}px`,
      left:      `${(W - H) / 2}px`,
      transform: `rotate(${r}deg)`,
    }
  }
  return {
    position:  'absolute' as const,
    inset:     '0',
    transform: r !== 0 ? `rotate(${r}deg)` : undefined,
  }
})

// ── Mini-widget positioning (object-fit:contain letterbox math) ───────────────
// The inner div dimensions before CSS rotation determine where the image lands.
// For 0°/180° the inner div fills the wrapper; for 90°/270° its dimensions are swapped.

const innerW = computed(() =>
  (rotation.value === 90 || rotation.value === 270) ? wrapperH.value : wrapperW.value
)
const innerH = computed(() =>
  (rotation.value === 90 || rotation.value === 270) ? wrapperW.value : wrapperH.value
)
// Scale at which the image is rendered inside the inner div
const imgScale = computed(() =>
  Math.min(innerW.value / naturalW.value, innerH.value / naturalH.value)
)
// Letterbox offsets (pixels of empty space on each side inside the inner div)
const imgOffX = computed(() => (innerW.value - naturalW.value * imgScale.value) / 2)
const imgOffY = computed(() => (innerH.value - naturalH.value * imgScale.value) / 2)

function miniWidgetStyle(mw: GrundrissMiniWidget) {
  return {
    position:      'absolute' as const,
    left:          `${imgOffX.value + mw.x * imgScale.value - mw.wPx / 2}px`,
    top:           `${imgOffY.value + mw.y * imgScale.value - mw.hPx / 2}px`,
    width:         `${mw.wPx}px`,
    height:        `${mw.hPx}px`,
    zIndex:        10,
    pointerEvents: (props.editorMode ? 'none' : 'auto') as 'none' | 'auto',
  }
}

// ── SVG helpers ───────────────────────────────────────────────────────────────

function polygonPointsStr(area: GrundrissArea): string {
  return area.points.map(([x, y]) => `${x},${y}`).join(' ')
}

// stroke-width and font-size in SVG viewport units (proportional to naturalW)
const svgStrokeW   = computed(() => naturalW.value * 0.0025)
const labelFontSz  = computed(() => naturalW.value * 0.018)

// ── Area click action ─────────────────────────────────────────────────────────

function handleAreaClick(area: GrundrissArea) {
  if (area.actionType === 'navigate' && area.actionValue) {
    router.push({ name: 'viewer', params: { id: area.actionValue } })
  }
}
</script>

<template>
  <div ref="wrapperRef" class="relative w-full h-full overflow-hidden">

    <!-- Empty state -->
    <div
      v-if="!image"
      class="absolute inset-0 flex items-center justify-center bg-gray-900/20"
    >
      <span class="text-xs text-gray-500">Kein Bild konfiguriert</span>
    </div>

    <!-- Rotated inner: image + SVG overlay + mini-widgets move together -->
    <div v-if="image" :style="innerStyle">
      <img
        :src="image"
        class="w-full h-full"
        style="object-fit: contain; display: block;"
        alt=""
        draggable="false"
      />

      <!--
        SVG viewBox matches the image's native dimensions.
        preserveAspectRatio="xMidYMid meet" mirrors object-fit:contain,
        so polygon coordinates (in natural image pixels) align perfectly.
        In editor mode pointer-events are off so drag/resize still works.
      -->
      <svg
        class="absolute inset-0 w-full h-full"
        :viewBox="`0 0 ${naturalW} ${naturalH}`"
        preserveAspectRatio="xMidYMid meet"
        :style="{ pointerEvents: editorMode ? 'none' : 'all' }"
      >
        <g v-for="area in areas" :key="area.id">
          <!-- Polygon — transparent in viewer (but still receives pointer events) -->
          <polygon
            :points="polygonPointsStr(area)"
            :fill="editorMode ? 'rgba(59,130,246,0.1)' : 'transparent'"
            :stroke="editorMode ? '#3b82f6' : 'none'"
            :stroke-width="svgStrokeW"
            :style="{ cursor: !editorMode && area.actionType !== 'none' ? 'pointer' : 'default' }"
            @click="handleAreaClick(area)"
          />
          <!-- Area label -->
          <text
            v-if="area.showLabel && showAreaNames"
            :x="area.labelX"
            :y="area.labelY"
            text-anchor="middle"
            dominant-baseline="middle"
            :font-size="labelFontSz"
            :fill="area.labelColor || '#ffffff'"
            :stroke-width="labelFontSz * 0.18"
            stroke="rgba(0,0,0,0.65)"
            paint-order="stroke fill"
            style="pointer-events: none; user-select: none;"
          >{{ area.name }}</text>
        </g>
      </svg>

      <!-- Mini-widgets: absolutely positioned over the image using letterbox math -->
      <div
        v-for="mw in miniWidgets"
        :key="`mw-${mw.id}`"
        v-show="mw.visible || editorMode"
        :style="miniWidgetStyle(mw)"
        class="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-lg"
      >
        <component
          v-if="WidgetRegistry.get(mw.widgetType)"
          :is="WidgetRegistry.get(mw.widgetType)!.component"
          :config="mw.config"
          :datapoint-id="mw.datapointId"
          :value="mw.datapointId ? dpStore.getValue(mw.datapointId) : null"
          :status-value="mw.statusDatapointId ? dpStore.getValue(mw.statusDatapointId) : null"
          :editor-mode="editorMode"
          :h="Math.round(mw.hPx / 80)"
        />
        <div v-else class="flex items-center justify-center h-full text-xs text-gray-500">
          {{ mw.widgetType }}?
        </div>
      </div>
    </div>
  </div>
</template>
