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

// ── Letterbox / coordinate math ───────────────────────────────────────────────
// The inner div (the rotated layer) has swapped dimensions for 90°/270°.
// imgScale and offsets describe how the image lands inside that inner div.

const innerW = computed(() =>
  (rotation.value === 90 || rotation.value === 270) ? wrapperH.value : wrapperW.value
)
const innerH = computed(() =>
  (rotation.value === 90 || rotation.value === 270) ? wrapperW.value : wrapperH.value
)
const imgScale = computed(() =>
  Math.min(innerW.value / naturalW.value, innerH.value / naturalH.value)
)
const imgOffX = computed(() => (innerW.value - naturalW.value * imgScale.value) / 2)
const imgOffY = computed(() => (innerH.value - naturalH.value * imgScale.value) / 2)

// Convert image-pixel coordinates to wrapper-relative screen coordinates.
// These formulas are the closed-form result of: inner-div position → CSS rotation.
function imageToScreen(px: number, py: number): [number, number] {
  const s = imgScale.value
  const ox = imgOffX.value
  const oy = imgOffY.value
  const W = wrapperW.value
  const H = wrapperH.value
  switch (rotation.value) {
    case 90:  return [W - oy - py * s, ox + px * s]
    case 180: return [W - ox - px * s, H - oy - py * s]
    case 270: return [oy + py * s,     H - ox - px * s]
    default:  return [ox + px * s,     oy + py * s]
  }
}

// Label font size in screen pixels (proportional to rendered image width)
const labelFontSz = computed(() => Math.max(8, naturalW.value * 0.018 * imgScale.value))

function labelStyle(area: GrundrissArea) {
  const [sx, sy] = imageToScreen(area.labelX, area.labelY)
  return {
    position:   'absolute' as const,
    left:       `${sx}px`,
    top:        `${sy}px`,
    transform:  'translate(-50%, -50%)',
    fontSize:   `${labelFontSz.value}px`,
    fontWeight: '500',
    lineHeight: '1',
    color:      area.labelColor || '#ffffff',
    textShadow: '0 0 3px rgba(0,0,0,0.9), 0 0 7px rgba(0,0,0,0.6)',
    whiteSpace: 'nowrap' as const,
    pointerEvents: 'none' as const,
    userSelect: 'none' as const,
    zIndex:     5,
  }
}

function miniWidgetStyle(mw: GrundrissMiniWidget) {
  const [sx, sy] = imageToScreen(mw.x, mw.y)
  return {
    position:      'absolute' as const,
    left:          `${sx - mw.wPx / 2}px`,
    top:           `${sy - mw.hPx / 2}px`,
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

// stroke-width in SVG viewport units (proportional to naturalW)
const svgStrokeW = computed(() => naturalW.value * 0.0025)

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

    <!-- Rotated layer: image + polygon SVG (no labels — they must not rotate) -->
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
        preserveAspectRatio="xMidYMid meet" mirrors object-fit:contain so polygon
        coordinates (in natural image pixels) align perfectly with the rendered image.
        Labels are intentionally NOT rendered here — see the unrotated overlay below.
      -->
      <svg
        class="absolute inset-0 w-full h-full"
        :viewBox="`0 0 ${naturalW} ${naturalH}`"
        preserveAspectRatio="xMidYMid meet"
        :style="{ pointerEvents: editorMode ? 'none' : 'all' }"
      >
        <polygon
          v-for="area in areas"
          :key="area.id"
          :points="polygonPointsStr(area)"
          :fill="editorMode ? 'rgba(59,130,246,0.1)' : 'transparent'"
          :stroke="editorMode ? '#3b82f6' : 'none'"
          :stroke-width="svgStrokeW"
          :style="{ cursor: !editorMode && area.actionType !== 'none' ? 'pointer' : 'default' }"
          @click="handleAreaClick(area)"
        />
      </svg>
    </div>

    <!-- Unrotated overlay: area labels + mini-widgets
         Positioned using imageToScreen() so they sit over the correct image spot
         regardless of rotation, but remain visually upright. -->
    <template v-if="image">
      <!-- Area labels -->
      <template v-for="area in areas" :key="`lbl-${area.id}`">
        <div v-if="area.showLabel && showAreaNames" :style="labelStyle(area)">
          {{ area.name }}
        </div>
      </template>

      <!-- Mini-widgets -->
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
    </template>
  </div>
</template>
