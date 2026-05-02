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
const invPosition     = computed(() => (props.config.invert_position      as boolean) ?? false)
const invShutter      = computed(() => (props.config.invert_shutter       as boolean) ?? false)

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

// Kipp nur für Einzelflügelfenster auswerten — bei Türen/Schiebetüren ignorieren
const stateMain  = computed(() => {
  const tiltId = (mode.value === 'fenster' || mode.value === 'fenster_r') ? dpTilt.value : null
  return deriveState(dpContact.value, invContact.value, tiltId, invTilt.value)
})
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
  const raw = getNumber(dpPositionStatus.value) ?? getNumber(dpPosition.value)
  if (raw === null) return null
  return invPosition.value ? 100 - Math.max(0, Math.min(100, raw)) : raw
})

// Dachflächenfenster: Rollladen-Anzeigeposition
const shutterDisplayPct = computed<number>(() => {
  if (props.editorMode || !enableShutter.value) return 0
  const raw = getNumber(dpShutterStatus.value) ?? getNumber(dpShutter.value)
  if (raw === null) return 0
  const v = Math.max(0, Math.min(100, raw))
  return invShutter.value ? 100 - v : v
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
  if (mode.value === 'eintuer_l') return effectiveStateLeft.value
  if (mode.value === 'eintuer_r') return effectiveStateRight.value
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

// Halbe sichtbare Paneelhöhe (Drehachse Mitte, beide Hälften gleichmässig verkürzt)
// Innenhöhe = 42px → jede Hälfte = 21px. Bei 100% steht Scheibe hochkant → halfH = 1px
const roofHalfH = computed(() => {
  if (mode.value !== 'dachfenster') return 21
  return Math.max(1, Math.round(21 * Math.cos(openPct.value * Math.PI / 200)))
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
            <circle cx="80" cy="30" r="1.5"/>
            <line x1="80" y1="30" x2="80" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'tilted'">
          <!-- Kipp: Drehachse unten, Oberkante kippt nach innen (links) -->
          <polygon points="27,5 77,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Griff 3px vom Rand, Arm parallel zur Kante (77,5)→(85,55): dx=−2 pro 10px -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="78" cy="30" r="1.5"/>
            <line x1="78" y1="30" x2="76" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="35,5 75,11 75,61 35,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (35,5)→(75,11) = 6/40 → −2px y pro 10px x -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="70" cy="36" r="1.5"/>
            <line x1="70" y1="36" x2="60" y2="34" stroke-width="2" stroke-linecap="round"/>
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
            <circle cx="40" cy="30" r="1.5"/>
            <line x1="40" y1="30" x2="40" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'tilted'">
          <!-- Kipp R-angeschlagen: Spiegel zu L-angeschlagen → Oberkante verschiebt sich RECHTS (+8)
               Polygon: oben x=43..93, unten x=35..85. Freie Linkskante bei y=30: x=39
               Griff 3px vom Rand, Arm parallel zur Kante (43,5)→(35,55): dx=+2 pro 10px -->
          <polygon points="43,5 93,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="42" cy="30" r="1.5"/>
            <line x1="42" y1="30" x2="44" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="85,5 45,11 45,61 85,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (85,5)→(45,11) = 6/40 → −2px y pro 10px x Richtung Scharnier -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="50" cy="36" r="1.5"/>
            <line x1="50" y1="36" x2="60" y2="34" stroke-width="2" stroke-linecap="round"/>
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
            <circle cx="50" cy="30" r="1.5"/>
            <line x1="50" y1="30" x2="50" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'tilted'">
          <!-- Freie rechte Kante: (47,5)→(55,55), bei y=30 → x=51. Griff 3px vom Rand, Arm dx=−2 pro 10px -->
          <polygon points="-3,5 47,5 55,55 5,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="48" cy="30" r="1.5"/>
            <line x1="48" y1="30" x2="46" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'open'">
          <polygon points="5,5 45,11 45,61 5,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="40" cy="36" r="1.5"/>
            <line x1="40" y1="36" x2="30" y2="34" stroke-width="2" stroke-linecap="round"/>
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
            <circle cx="70" cy="30" r="1.5"/>
            <line x1="70" y1="30" x2="70" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'tilted'">
          <!-- Kipp R-angeschlagen: Spiegel zu linkem Flügel → Oberkante verschiebt sich RECHTS (+8)
               Polygon: oben x=73..123, unten x=65..115. Freie Linkskante bei y=30: x=69
               Griff 3px vom Rand, Arm parallel zur Kante (73,5)→(65,55): dx=+2 pro 10px -->
          <polygon points="73,5 123,5 115,55 65,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="72" cy="30" r="1.5"/>
            <line x1="72" y1="30" x2="74" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'open'">
          <polygon points="115,5 75,11 75,61 115,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="80" cy="36" r="1.5"/>
            <line x1="80" y1="36" x2="90" y2="34" stroke-width="2" stroke-linecap="round"/>
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
            <circle cx="76" cy="100" r="2"/>
            <line x1="76" y1="100" x2="76" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'tilted'">
          <!-- Freie rechte Kante: (70,7)→(83,190), bei y=100 → x≈77. Griff 5px vom Rand, Arm dx=−1 pro 15px -->
          <polygon points="-6,7 70,7 83,190 7,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="72" cy="100" r="2"/>
            <line x1="72" y1="100" x2="71" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="60" cy="107" r="2"/>
            <line x1="60" y1="107" x2="45" y2="105" stroke-width="3" stroke-linecap="round"/>
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
            <circle cx="104" cy="100" r="2"/>
            <line x1="104" y1="100" x2="104" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'tilted'">
          <!-- Freie linke Kante (R-angeschlagen): (110,7)→(97,190), bei y=100 → x≈103. Griff 5px vom Rand, Arm dx=+1 pro 15px -->
          <polygon points="110,7 186,7 173,190 97,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="108" cy="100" r="2"/>
            <line x1="108" y1="100" x2="109" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'open'">
          <polygon points="173,7 113,16 113,199 173,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="120" cy="107" r="2"/>
            <line x1="120" y1="107" x2="135" y2="105" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="135" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Single door LEFT-hinged, Zweitürer-style (eintuer_l) ────────── -->
      <!--
        Gleiche Geometrie wie linker Flügel des Zweitürers (76×183cm Türblatt).
        viewBox 92×200. Rahmen x=2..90, y=2..194. Boden y=196.
        Anschlag links (x=2), freie Kante rechts (x=90).
      -->
      <!-- ── Single door LEFT-hinged, Zweitürer-style (eintuer_l) ────────── -->
      <!--
        Gleiche Geometrie wie linker Flügel des Zweitürers inkl. Kipp.
        viewBox "-8 0 106 200" — Kipp-Polygon ragt links bis x=-6 hinaus.
      -->
      <svg
        v-else-if="mode === 'eintuer_l'"
        viewBox="-8 0 106 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <line x1="2"  y1="2"   x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="90" y1="2"   x2="90" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="2"   x2="90" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="196" x2="90" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <template v-if="effectiveStateLeft === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="76" cy="100" r="2"/>
            <line x1="76" y1="100" x2="76" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'tilted'">
          <!-- Freie rechte Kante: (70,7)→(83,190), bei y=100 → x≈77. Griff 5px vom Rand, Arm dx=−1 pro 15px -->
          <polygon points="-6,7 70,7 83,190 7,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="72" cy="100" r="2"/>
            <line x1="72" y1="100" x2="71" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateLeft === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (7,7)→(67,16) = 9/60 → −2px y pro 15px x -->
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="60" cy="107" r="2"/>
            <line x1="60" y1="107" x2="45" y2="105" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="46" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Single door RIGHT-hinged, Zweitürer-style (eintuer_r) ───────── -->
      <!--
        Gespiegelt zu eintuer_l. Kipp-Polygon ragt rechts bis x=96 hinaus.
      -->
      <svg
        v-else-if="mode === 'eintuer_r'"
        viewBox="-8 0 106 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <line x1="2"  y1="2"   x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="90" y1="2"   x2="90" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="2"   x2="90" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <line x1="2"  y1="196" x2="90" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <template v-if="effectiveStateRight === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="14" cy="100" r="2"/>
            <line x1="14" y1="100" x2="14" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'tilted'">
          <!-- Freie linke Kante (R-angeschlagen): (20,7)→(7,190), bei y=100 → x≈13. Griff 5px vom Rand, Arm dx=+1 pro 15px -->
          <polygon points="96,7 20,7 7,190 83,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="18" cy="100" r="2"/>
            <line x1="18" y1="100" x2="19" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="effectiveStateRight === 'open'">
          <polygon points="83,7 23,16 23,199 83,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (83,7)→(23,16) = 9/60 → −2px y pro 15px x Richtung Scharnier -->
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="30" cy="107" r="2"/>
            <line x1="30" y1="107" x2="45" y2="105" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="46" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
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
            <circle cx="76" cy="100" r="2"/>
            <line x1="76" y1="100" x2="61" y2="100" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (7,7)→(67,16) = 9/60 → −2px y pro 15px x -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="60" cy="107" r="2"/>
            <line x1="60" y1="107" x2="45" y2="105" stroke-width="3" stroke-linecap="round"/>
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
            <circle cx="14" cy="100" r="2"/>
            <line x1="14" y1="100" x2="29" y2="100" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'open'">
          <polygon points="83,7 23,16 23,199 83,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Arm parallel zur Paneloberfläche: Steigung (83,7)→(23,16) = 9/60 → −2px y pro 15px x Richtung Scharnier -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="30" cy="107" r="2"/>
            <line x1="30" y1="107" x2="45" y2="105" stroke-width="3" stroke-linecap="round"/>
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
            <circle cx="184" cy="48" r="2"/>
            <line x1="184" y1="48" x2="184" y2="25" stroke-width="3" stroke-linecap="round"/>
          </g>
          <g v-else class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="16" cy="48" r="2"/>
            <line x1="16" y1="48" x2="16" y2="25" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <!-- Open, fixer Teil LINKS: panel slid left (solid), gap right (ghost) -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer'">
          <rect x="7"   y="7" width="93" height="82" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <rect x="100" y="7" width="93" height="82" stroke-width="1.5" stroke-dasharray="8,5"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="93" cy="48" r="2"/>
            <line x1="93" y1="48" x2="93" y2="71" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <!-- Open, fixer Teil RECHTS: gap left (ghost), panel slid right (solid) -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer_r'">
          <rect x="7"   y="7" width="93" height="82" stroke-width="1.5" stroke-dasharray="8,5"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
          <rect x="100" y="7" width="93" height="82" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="107" cy="48" r="2"/>
            <line x1="107" y1="48" x2="107" y2="71" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="100" y="50" text-anchor="middle" dominant-baseline="middle" font-size="30" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Dachflächenfenster (dachfenster) ─────────────────────────── -->
      <!--
        Horizontale Drehachse in der Mitte (center-pivot, Velux-Typ).
        viewBox 72×56. Rahmen rect(2,2,68,52). Innenfläche: x=7…65, y=7…49 (h=42).
        Drehachse bei y=28 (Mitte). Beide Hälften kürzen sich gleichmässig ein.
        0% = geschlossen (Vollfläche), 100% = hochkant (1px Streifen).
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

        <!-- Geschlossen: volle Scheibe + subtile Drehachsen-Linie -->
        <template v-if="roofState === 'closed'">
          <rect x="7" y="7" width="58" height="42" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <line x1="7" y1="28" x2="65" y2="28" stroke="currentColor" stroke-width="0.5" opacity="0.25"/>
        </template>

        <!-- Teilweise/ganz offen: Cosinus-Verkürzung um Mittelachse -->
        <template v-else-if="roofState !== 'unknown'">
          <!-- Geisterumriss der vollen Innenfläche -->
          <rect x="7" y="7" width="58" height="42" stroke-width="1" stroke-dasharray="2,2"
                class="fill-gray-100 dark:fill-gray-800 stroke-gray-300 dark:stroke-gray-600" opacity="0.4"/>
          <!-- Drehachse (horizontal, Mitte) -->
          <line x1="7" y1="28" x2="65" y2="28"
                stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity="0.5"/>
          <!-- Verkürzte Scheibe: beide Hälften symmetrisch um y=28 -->
          <rect x="7" :y="28 - roofHalfH" width="58" :height="roofHalfH * 2" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- Öffnungsgrad in Prozent (unterhalb der Drehachse) -->
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

        <!-- Rollladen-Overlay: dunkler Hintergrund (Lamellenrücken) + helle Lamellenstreifen (3px + 1px Schatten) -->
        <template v-if="enableShutter && shutterBarH > 0">
          <rect x="7" y="7" width="58" :height="shutterBarH"
                class="fill-gray-600 dark:fill-gray-500" opacity="0.95"/>
          <rect
            v-for="i in shutterSlatCount" :key="i"
            x="7" :y="7 + (i - 1) * 4" width="58" height="3"
            class="fill-gray-300 dark:fill-gray-300" opacity="0.85"
          />
        </template>
      </svg>

    </div>
  </div>
</template>
