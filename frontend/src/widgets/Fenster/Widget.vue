<script setup lang="ts">
import { computed } from 'vue'
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

const label = computed(() => (props.config.label as string) ?? '—')
const mode  = computed(() => (props.config.mode  as string) ?? 'fenster')

// Datapoint IDs
const dpContact      = computed(() => (props.config.dp_contact       as string) || null)
const dpTilt         = computed(() => (props.config.dp_tilt          as string) || null)
const dpContactLeft  = computed(() => (props.config.dp_contact_left  as string) || null)
const dpTiltLeft     = computed(() => (props.config.dp_tilt_left     as string) || null)
const dpContactRight = computed(() => (props.config.dp_contact_right as string) || null)
const dpTiltRight    = computed(() => (props.config.dp_tilt_right    as string) || null)
const dpPosition     = computed(() => (props.config.dp_position      as string) || null)

// Dachflächenfenster: separate Status-DPs
const dpPositionStatus = computed(() => (props.config.dp_position_status as string) || null)
const dpShutter        = computed(() => (props.config.dp_shutter         as string) || null)
const dpShutterStatus  = computed(() => (props.config.dp_shutter_status  as string) || null)
const enableShutter    = computed(() => (props.config.enable_shutter     as boolean) ?? false)

// Invert flags
const invContact      = computed(() => (props.config.invert_contact       as boolean) ?? false)
const invTilt         = computed(() => (props.config.invert_tilt          as boolean) ?? false)
const invContactLeft  = computed(() => (props.config.invert_contact_left  as boolean) ?? false)
const invTiltLeft     = computed(() => (props.config.invert_tilt_left     as boolean) ?? false)
const invContactRight = computed(() => (props.config.invert_contact_right as boolean) ?? false)
const invTiltRight    = computed(() => (props.config.invert_tilt_right    as boolean) ?? false)

function getBool(id: string | null, invert = false): boolean | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v || v.v === null || v.v === undefined) return null
  let result: boolean | null = null
  if (typeof v.v === 'boolean') result = v.v
  else if (typeof v.v === 'number') result = v.v !== 0
  else {
    const s = String(v.v).toLowerCase()
    if (s === 'true'  || s === '1') result = true
    else if (s === 'false' || s === '0') result = false
  }
  return result === null ? null : (invert ? !result : result)
}

function getNumber(id: string | null): number | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v) return null
  if (typeof v.v === 'number') return v.v
  const p = parseFloat(String(v.v))
  return isNaN(p) ? null : p
}

type WinState = 'closed' | 'tilted' | 'open' | 'unknown'

function deriveState(
  contactId: string | null, invC: boolean,
  tiltId:   string | null, invT: boolean,
): WinState {
  if (props.editorMode) return 'closed'
  const tilt    = getBool(tiltId, invT)
  const contact = getBool(contactId, invC)
  if (tilt === true)     return 'tilted'
  if (contact === true)  return 'open'
  if (contact === false) return 'closed'
  if (contactId === null && tiltId === null) return 'unknown'
  return 'unknown'
}

const stateMain  = computed(() => deriveState(dpContact.value, invContact.value, dpTilt.value, invTilt.value))
const stateLeft  = computed(() => deriveState(dpContactLeft.value, invContactLeft.value, dpTiltLeft.value, invTiltLeft.value))
const stateRight = computed(() => deriveState(dpContactRight.value, invContactRight.value, dpTiltRight.value, invTiltRight.value))

// Handle visibility (fenster_2 / zweituerer)
const showHandleLeft  = computed(() => (props.config.handle_left  as boolean) ?? true)
const showHandleRight = computed(() => (props.config.handle_right as boolean) ?? true)

// Wenn Griff deaktiviert → Flügel intern immer als geschlossen klassifizieren
const effectiveStateLeft = computed<WinState>(() =>
  !showHandleLeft.value ? 'closed' : stateLeft.value
)
const effectiveStateRight = computed<WinState>(() =>
  !showHandleRight.value ? 'closed' : stateRight.value
)

// Dachflächenfenster: Anzeige-Position bevorzugt dp_position_status, Fallback dp_position
const displayPosition = computed<number | null>(() => {
  if (props.editorMode) return null
  return getNumber(dpPositionStatus.value) ?? getNumber(dpPosition.value)
})

// Dachflächenfenster: Rollladen-Anzeigeposition
const shutterDisplayPct = computed<number>(() => {
  if (props.editorMode || !enableShutter.value) return 0
  const v = getNumber(dpShutterStatus.value) ?? getNumber(dpShutter.value)
  if (v === null) return 0
  return Math.max(0, Math.min(100, v))
})

// Dachflächenfenster: Zustand nur aus Position (kein Kontakt-Fallback mehr)
const roofState = computed<WinState>(() => {
  if (props.editorMode) return 'closed'
  const pos = displayPosition.value
  if (pos !== null) {
    if (pos <= 0)   return 'closed'
    if (pos >= 100) return 'open'
    return 'tilted'
  }
  return 'unknown'
})

// Custom state colors from config (hex), fallback to green/orange/red
const colorClosed = computed(() => (props.config.color_closed as string) || '#16a34a')
const colorTilted = computed(() => (props.config.color_tilted as string) || '#f97316')
const colorOpen   = computed(() => (props.config.color_open   as string) || '#ef4444')

function stateColor(s: WinState): string {
  switch (s) {
    case 'closed': return colorClosed.value
    case 'tilted': return colorTilted.value
    case 'open':   return colorOpen.value
    default:       return '#9ca3af'
  }
}

function stateColorStyle(s: WinState): { color: string } {
  return { color: stateColor(s) }
}

const summaryState = computed<WinState>(() => {
  if (mode.value === 'fenster_2' || mode.value === 'zweituerer') {
    if (effectiveStateLeft.value === 'open'   || effectiveStateRight.value === 'open')   return 'open'
    if (effectiveStateLeft.value === 'tilted' || effectiveStateRight.value === 'tilted') return 'tilted'
    if (effectiveStateLeft.value === 'closed' && effectiveStateRight.value === 'closed') return 'closed'
    return 'unknown'
  }
  if (mode.value === 'dachfenster') return roofState.value
  return stateMain.value
})

const colorStyle = computed(() => stateColorStyle(summaryState.value))

// Opening percentage (0-100) for roof window rendering
const openPct = computed(() => {
  if (mode.value !== 'dachfenster') return 0
  const pos = displayPosition.value
  if (pos === null) return 0
  return Math.max(0, Math.min(100, pos))
})

// Sichtbare Paneelhöhe (perspektivische Verkürzung bei geöffnetem Dachflächenfenster)
// Drehachse oben: Scheibe klappt nach unten weg, bei 100% nur noch schmaler Streifen
const roofPaneH = computed(() => {
  if (mode.value !== 'dachfenster') return 42
  return Math.max(2, Math.round(42 * Math.cos(openPct.value * Math.PI / 200)))
})

// Rollladenhöhe in SVG-Einheiten (42 = volle Innenhöhe)
const shutterBarH = computed(() =>
  Math.min(42, Math.round(42 * shutterDisplayPct.value / 100))
)

// Lamellen-Anzahl (alle 4 SVG-Einheiten eine Linie)
const shutterSlatCount = computed(() => Math.floor(shutterBarH.value / 4))
</script>

<template>
  <div class="flex flex-col h-full p-2 select-none gap-1" :style="colorStyle">
    <!-- Label -->
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate leading-none">{{ label }}</span>

    <!-- SVG area -->
    <div class="flex-1 flex items-center justify-center min-h-0 min-w-0">

      <!-- ── Single-wing window LEFT-hinged (fenster) ──────────────────── -->
      <!--
        Real: 60×60cm  →  viewBox 60×60  (1cm = 1unit)
        Frame stroke 2.5 (4.2% of 60)  |  pane stroke 1.5  |  handle r=2
        Pane area: x 5→55 (w=50), y 5→55 (h=50)
        Kipp shift: 17% of 50 = 8.5 → top-left at x=-3 (clips at viewBox edge)
        Open: 79% of 50 = 40px from hinge, perspective fall +6px on free side
      -->
      <svg
        v-if="mode === 'fenster'"
        viewBox="0 -2 120 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- content centred: original 60×60 shifted +30 on x -->
        <rect x="31.5" y="1.5" width="57" height="57" rx="0.5" stroke-width="2.5" stroke="currentColor"/>

        <template v-if="stateMain === 'closed'">
          <rect x="35" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="83" cy="30" r="1.5"/>
            <line x1="83" y1="30" x2="83" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'tilted'">
          <!-- Kipp: Drehachse unten, Oberkante kippt nach innen (links) -->
          <polygon points="27,5 77,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Griff auf freier rechter Kante (x=81 bei y=30), Arm parallel zur Kante (77,5)→(85,55): dx=−2 pro 10px -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="81" cy="30" r="1.5"/>
            <line x1="81" y1="30" x2="79" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="35,5 75,11 75,61 35,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="73" cy="36" r="1.5"/>
            <line x1="73" y1="36" x2="63" y2="36" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="60" y="30" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Single-wing window RIGHT-hinged (fenster_r) ──────────────── -->
      <svg
        v-else-if="mode === 'fenster_r'"
        viewBox="0 -2 120 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <rect x="31.5" y="1.5" width="57" height="57" rx="0.5" stroke-width="2.5" stroke="currentColor"/>

        <template v-if="stateMain === 'closed'">
          <rect x="35" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="37" cy="30" r="1.5"/>
            <line x1="37" y1="30" x2="37" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'tilted'">
          <!-- Kipp R-angeschlagen: Spiegel zu L-angeschlagen → Oberkante verschiebt sich RECHTS (+8)
               Polygon: oben x=43..93, unten x=35..85. Freie Linkskante bei y=30: x=39
               Arm parallel zur Kante (43,5)→(35,55): dx=+2 pro 10px -->
          <polygon points="43,5 93,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="39" cy="30" r="1.5"/>
            <line x1="39" y1="30" x2="41" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="85,5 45,11 45,61 85,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="47" cy="36" r="1.5"/>
            <line x1="47" y1="36" x2="57" y2="36" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="60" y="30" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Double-wing window (fenster_2) ──────────────────────────── -->
      <!--
        Real: 120×60cm  →  viewBox 120×60  (1cm = 1unit)
        Each wing = 60×60cm = identical to single fenster (pane x=5→55 / x=65→115, w=50, h=50)
        Frame split L/R per wing state. Center divider at x=60. Strokes same as fenster.
        Griff deaktiviert → Flügel immer als geschlossen klassifiziert (effectiveState)
      -->
      <svg
        v-else-if="mode === 'fenster_2'"
        viewBox="-10 -2 140 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Left half-frame (effectiveStateLeft color) -->
        <g :style="stateColorStyle(effectiveStateLeft)">
          <line x1="1.5" y1="1.5" x2="1.5"  y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="1.5" y1="1.5" x2="60"   y2="1.5"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="1.5" y1="58.5" x2="60"  y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>
        <!-- Right half-frame (effectiveStateRight color) -->
        <g :style="stateColorStyle(effectiveStateRight)">
          <line x1="118.5" y1="1.5" x2="118.5" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="60"    y1="1.5" x2="118.5" y2="1.5"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="60"    y1="58.5" x2="118.5" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>
        <!-- Center divider (summaryState) -->
        <line x1="60" y1="1.5" x2="60" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>

        <!-- Left wing pane — L-hinged -->
        <template v-if="effectiveStateLeft === 'closed'">
          <rect x="5" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="53" cy="30" r="1.5"/>
            <line x1="53" y1="30" x2="53" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'tilted'">
          <!-- Freie rechte Kante: (47,5)→(55,55), bei y=30 → x=51. Arm parallel zur Kante: dx=−2 pro 10px -->
          <polygon points="-3,5 47,5 55,55 5,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="51" cy="30" r="1.5"/>
            <line x1="51" y1="30" x2="49" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'open'">
          <polygon points="5,5 45,11 45,61 5,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="43" cy="36" r="1.5"/>
            <line x1="43" y1="36" x2="33" y2="36" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="30" y="30" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>

        <!-- Right wing pane — R-hinged, offset +60 -->
        <template v-if="effectiveStateRight === 'closed'">
          <rect x="65" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="67" cy="30" r="1.5"/>
            <line x1="67" y1="30" x2="67" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'tilted'">
          <!-- Kipp R-angeschlagen: Spiegel zu linkem Flügel → Oberkante verschiebt sich RECHTS (+8)
               Polygon: oben x=73..123, unten x=65..115. Freie Linkskante bei y=30: x=69
               Arm parallel zur Kante (73,5)→(65,55): dx=+2 pro 10px -->
          <polygon points="73,5 123,5 115,55 65,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="69" cy="30" r="1.5"/>
            <line x1="69" y1="30" x2="71" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'open'">
          <polygon points="115,5 75,11 75,61 115,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="77" cy="36" r="1.5"/>
            <line x1="77" y1="36" x2="87" y2="36" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="90" y="30" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Double door (zweituerer) ──────────────────────────────────── -->
      <!--
        Real: 2×90×200cm  →  viewBox 180×200  (1cm = 1unit)
        Griff deaktiviert → Flügel immer als geschlossen klassifiziert (effectiveState)
      -->
      <svg
        v-else-if="mode === 'zweituerer'"
        viewBox="-10 0 200 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Left half-frame (effectiveStateLeft color) -->
        <g :style="stateColorStyle(effectiveStateLeft)">
          <line x1="2"  y1="2"  x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
          <line x1="2"  y1="2"  x2="90" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        </g>
        <!-- Center divider (summaryState) -->
        <line x1="90" y1="2" x2="90" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <!-- Right half-frame (effectiveStateRight color) -->
        <g :style="stateColorStyle(effectiveStateRight)">
          <line x1="178" y1="2"   x2="178" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
          <line x1="90"  y1="2"   x2="178" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        </g>
        <!-- Floor line (thin, semi-transparent) -->
        <line x1="2" y1="196" x2="178" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <!-- Left wing pane (L-hinged) -->
        <template v-if="effectiveStateLeft === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="81" cy="100" r="2"/>
            <line x1="81" y1="100" x2="81" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'tilted'">
          <!-- Freie rechte Kante: (70,7)→(83,190), bei y=100 → x≈77. Arm parallel zur Kante: dx=−1 pro 15px -->
          <polygon points="-6,7 70,7 83,190 7,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="77" cy="100" r="2"/>
            <line x1="77" y1="100" x2="76" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="65" cy="107" r="2"/>
            <line x1="65" y1="107" x2="50" y2="107" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="45" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>

        <!-- Right wing pane (R-hinged, tuere_r offset +90) -->
        <template v-if="effectiveStateRight === 'closed'">
          <rect x="97" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="99" cy="100" r="2"/>
            <line x1="99" y1="100" x2="99" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'tilted'">
          <!-- Freie linke Kante (R-angeschlagen): (110,7)→(97,190), bei y=100 → x≈103. Arm parallel zur Kante: dx=+1 pro 15px -->
          <polygon points="110,7 186,7 173,190 97,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="103" cy="100" r="2"/>
            <line x1="103" y1="100" x2="104" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'open'">
          <polygon points="173,7 113,16 113,199 173,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="115" cy="107" r="2"/>
            <line x1="115" y1="107" x2="130" y2="107" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="135" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Door LEFT-hinged (tuere) ──────────────────────────────────── -->
      <!--
        Real: 90×200cm  →  viewBox 90×200  (1cm = 1unit)
        Frame stroke 4 (4.4% of 90)  |  pane stroke 2  |  handle r=3
        Pane: x=7, y=7, w=76, h=183 (to y=190)
        Open: 79% of 76 = 60px, fall 9px. Free side bottom at y=199 (inside viewBox)
      -->
      <svg
        v-else-if="mode === 'tuere'"
        viewBox="0 0 90 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <line x1="2"  y1="2"   x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="88" y1="2"   x2="88" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="2"   x2="88" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="196" x2="88" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle on free (right) edge at ~100cm from floor, always points LEFT (Anschlag links) -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="81" cy="100" r="2"/>
            <line x1="81" y1="100" x2="66" y2="100" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="65" cy="107" r="2"/>
            <line x1="65" y1="107" x2="50" y2="107" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="45" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Door RIGHT-hinged (tuere_r) ──────────────────────────────── -->
      <svg
        v-else-if="mode === 'tuere_r'"
        viewBox="0 0 90 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <line x1="2"  y1="2"   x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="88" y1="2"   x2="88" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="2"   x2="88" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="196" x2="88" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle on free (left) edge, always points RIGHT (Anschlag rechts) -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="9"  cy="100" r="2"/>
            <line x1="9"  y1="100" x2="24" y2="100" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="83,7 23,16 23,199 83,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="25" cy="107" r="2"/>
            <line x1="25" y1="107" x2="40" y2="107" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="45" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Sliding door (schiebetuer) ──────────────────────────────── -->
      <!--
        Real: 400×200cm  →  viewBox 200×100  (1cm = 0.5unit, same 2:1 ratio)
        Frame stroke 4 (4% of 100)  |  panel stroke 2
        Pane area: x=7, y=7, w=186, h=82 (to y=89). Center at x=100.
        Each half: w=93. Ghost dasharray "8,5" (scaled for 200-unit viewBox).
      -->
      <svg
        v-else-if="mode === 'schiebetuer' || mode === 'schiebetuer_r'"
        viewBox="0 0 200 100"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <line x1="2"   y1="2"  x2="2"   y2="94" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="198" y1="2"  x2="198" y2="94" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"   y1="2"  x2="198" y2="2"  stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"   y1="96" x2="198" y2="96" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="186" height="82" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="mode === 'schiebetuer'" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="188" cy="48" r="2"/>
            <line x1="188" y1="48" x2="188" y2="25" stroke-width="3" stroke-linecap="round"/>
          </g>
          <g v-else class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="12" cy="48" r="2"/>
            <line x1="12" y1="48" x2="12" y2="25" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <!-- Open, fixer Teil LINKS: panel slid left (solid), gap right (ghost) -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer'">
          <rect x="7"   y="7" width="93" height="82" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <rect x="100" y="7" width="93" height="82" stroke-width="1.5" stroke-dasharray="8,5"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="97" cy="48" r="2"/>
            <line x1="97" y1="48" x2="97" y2="71" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <!-- Open, fixer Teil RECHTS: gap left (ghost), panel slid right (solid) -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer_r'">
          <rect x="7"   y="7" width="93" height="82" stroke-width="1.5" stroke-dasharray="8,5"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
          <rect x="100" y="7" width="93" height="82" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="103" cy="48" r="2"/>
            <line x1="103" y1="48" x2="103" y2="71" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="100" y="50" text-anchor="middle" dominant-baseline="middle" font-size="30" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Dachflächenfenster (dachfenster) ─────────────────────────── -->
      <!--
        viewBox 72×56. Rahmen rect(2,2,68,52). Innenfläche: x=7…65, y=7…49 (w=58, h=42).
        Drehachse oben (y=7): Scheibe klappt nach unten weg.
        0% = geschlossen (Vollfläche), 100% = schmaler Streifen (Cosinus-Projektion).
        Optional: Rollladen-Overlay von oben nach unten.
      -->
      <svg
        v-else-if="mode === 'dachfenster'"
        viewBox="0 0 72 56"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="68" height="52" rx="1" stroke="currentColor" stroke-width="2.5"/>

        <!-- Geschlossen: volle Scheibe -->
        <template v-if="roofState === 'closed'">
          <rect x="7" y="7" width="58" height="42" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
        </template>

        <!-- Teilweise/ganz offen: perspektivisch verkürzte Scheibe -->
        <template v-else-if="roofState !== 'unknown'">
          <!-- Geisterumriss der vollen Innenfläche -->
          <rect x="7" y="7" width="58" height="42" stroke-width="1" stroke-dasharray="2,2"
                class="fill-gray-100 dark:fill-gray-800 stroke-gray-300 dark:stroke-gray-600" opacity="0.4"/>
          <!-- Verkürzte Scheibe (Cosinus-Projektion, Drehachse oben) -->
          <rect x="7" y="7" width="58" :height="roofPaneH" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Unterkante der sichtbaren Scheibe hervorheben -->
          <line x1="7" :y1="7 + roofPaneH" x2="65" :y2="7 + roofPaneH"
                stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity="0.6"/>
          <!-- Öffnungsgrad in Prozent -->
          <text
            v-if="displayPosition !== null"
            x="36" y="44"
            text-anchor="middle" dominant-baseline="middle"
            font-size="10" fill="currentColor" opacity="0.8"
          >{{ Math.round(openPct) }}%</text>
        </template>

        <!-- Unbekannter Zustand (kein Datenpunkt) -->
        <template v-else>
          <text x="36" y="28" text-anchor="middle" dominant-baseline="middle"
                font-size="16" fill="currentColor" opacity="0.4">?</text>
        </template>

        <!-- Rollladen-Overlay (optional, von oben nach unten) -->
        <template v-if="enableShutter && shutterBarH > 0">
          <!-- Rollladen-Fläche -->
          <rect x="7" y="7" width="58" :height="shutterBarH"
                class="fill-gray-500 dark:fill-gray-400" opacity="0.75"/>
          <!-- Lamellen-Linien alle 4 SVG-Einheiten -->
          <line
            v-for="i in shutterSlatCount" :key="i"
            x1="7" :y1="7 + i * 4" x2="65" :y2="7 + i * 4"
            stroke="currentColor" stroke-width="0.5" opacity="0.4"
          />
        </template>
      </svg>

    </div>
  </div>
</template>
