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

const position = computed<number | null>(() => {
  if (props.editorMode) return null
  return getNumber(dpPosition.value)
})

// Roof window: derive state from position if no contact datapoint given
const roofState = computed<WinState>(() => {
  if (props.editorMode) return 'closed'
  const pos = position.value
  if (pos !== null) {
    if (pos <= 0)   return 'closed'
    if (pos >= 100) return 'open'
    return 'tilted'
  }
  return deriveState(dpContact.value, invContact.value, dpTilt.value, invTilt.value)
})

// Green = closed, Orange = tilted, Red = open
function stateColorClass(s: WinState): string {
  switch (s) {
    case 'closed':  return 'text-green-600 dark:text-green-400'
    case 'tilted':  return 'text-orange-500 dark:text-orange-400'
    case 'open':    return 'text-red-500 dark:text-red-400'
    default:        return 'text-gray-400 dark:text-gray-500'
  }
}

const summaryState = computed<WinState>(() => {
  if (mode.value === 'fenster_2' || mode.value === 'zweituerer') {
    if (stateLeft.value === 'open'   || stateRight.value === 'open')   return 'open'
    if (stateLeft.value === 'tilted' || stateRight.value === 'tilted') return 'tilted'
    if (stateLeft.value === 'closed' && stateRight.value === 'closed') return 'closed'
    return 'unknown'
  }
  if (mode.value === 'dachfenster') return roofState.value
  return stateMain.value
})

const colorClass = computed(() => stateColorClass(summaryState.value))

// Handle visibility (fenster_2 only)
const showHandleLeft  = computed(() => (props.config.handle_left  as boolean) ?? true)
const showHandleRight = computed(() => (props.config.handle_right as boolean) ?? true)

// Opening percentage (0-100) for roof window gap rendering
const openPct = computed(() => {
  if (mode.value !== 'dachfenster') return 0
  const pos = position.value
  if (pos === null) {
    if (roofState.value === 'open')   return 100
    if (roofState.value === 'tilted') return 40
    return 0
  }
  return Math.max(0, Math.min(100, pos))
})
</script>

<template>
  <div class="flex flex-col h-full p-2 select-none gap-1" :class="colorClass">
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
        viewBox="0 0 120 60"
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
          <polygon points="27,5 77,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="81" cy="30" r="1.5"/>
            <line x1="81" y1="30" x2="81" y2="20" stroke-width="2" stroke-linecap="round"/>
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
      <!-- Real: 60×60cm → viewBox 60×60. Mirror of fenster. -->
      <svg
        v-else-if="mode === 'fenster_r'"
        viewBox="0 0 120 60"
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
            <circle cx="37" cy="30" r="1.5"/>
            <line x1="37" y1="30" x2="37" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateMain === 'tilted'">
          <polygon points="27,5 77,5 85,55 35,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="39" cy="30" r="1.5"/>
            <line x1="39" y1="30" x2="39" y2="20" stroke-width="2" stroke-linecap="round"/>
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
      -->
      <svg
        v-else-if="mode === 'fenster_2'"
        viewBox="0 0 120 60"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Left half-frame (stateLeft color) -->
        <g :class="stateColorClass(stateLeft)">
          <line x1="1.5" y1="1.5" x2="1.5"  y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="1.5" y1="1.5" x2="60"   y2="1.5"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="1.5" y1="58.5" x2="60"  y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>
        <!-- Right half-frame (stateRight color) -->
        <g :class="stateColorClass(stateRight)">
          <line x1="118.5" y1="1.5" x2="118.5" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="60"    y1="1.5" x2="118.5" y2="1.5"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="60"    y1="58.5" x2="118.5" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>
        <!-- Center divider (summaryState) -->
        <line x1="60" y1="1.5" x2="60" y2="58.5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>

        <!-- Left wing pane — identical to single fenster (L-hinged) -->
        <template v-if="stateLeft === 'closed'">
          <rect x="5" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="53" cy="30" r="1.5"/>
            <line x1="53" y1="30" x2="53" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateLeft === 'tilted'">
          <polygon points="-3,5 47,5 55,55 5,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="51" cy="30" r="1.5"/>
            <line x1="51" y1="30" x2="51" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateLeft === 'open'">
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

        <!-- Right wing pane — R-hinged, offset +60, mirror of left -->
        <template v-if="stateRight === 'closed'">
          <rect x="65" y="5" width="50" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle on free (left) edge, arm DOWN -->
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="67" cy="30" r="1.5"/>
            <line x1="67" y1="30" x2="67" y2="40" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateRight === 'tilted'">
          <polygon points="57,5 107,5 115,55 65,55" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- arm UP = kipp -->
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="69" cy="30" r="1.5"/>
            <line x1="69" y1="30" x2="69" y2="20" stroke-width="2" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateRight === 'open'">
          <polygon points="115,5 75,11 75,61 115,55" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- arm RIGHT toward hinge (Anschlag rechts) = open -->
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
        Left wing = tuere (L-hinged), right wing = tuere_r (R-hinged, offset +90)
        Frame split L/R per wing state. Center divider at x=90. Strokes same as tuere.
      -->
      <svg
        v-else-if="mode === 'zweituerer'"
        viewBox="0 0 180 200"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Left half-frame (stateLeft color) -->
        <g :class="stateColorClass(stateLeft)">
          <line x1="2"  y1="2"  x2="2"  y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
          <line x1="2"  y1="2"  x2="90" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        </g>
        <!-- Center divider (summaryState) -->
        <line x1="90" y1="2" x2="90" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        <!-- Right half-frame (stateRight color) -->
        <g :class="stateColorClass(stateRight)">
          <line x1="178" y1="2"   x2="178" y2="194" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
          <line x1="90"  y1="2"   x2="178" y2="2"   stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
        </g>
        <!-- Floor line (thin, semi-transparent) -->
        <line x1="2" y1="196" x2="178" y2="196" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <!-- Left wing pane (L-hinged) -->
        <template v-if="stateLeft === 'closed'">
          <rect x="7" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: closed → DOWN -->
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="81" cy="100" r="2"/>
            <line x1="81" y1="100" x2="81" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateLeft === 'tilted'">
          <!-- kipp: bottom fixed, top shifts left 13 units (17% of 76) -->
          <polygon points="-6,7 70,7 83,190 7,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: kipp → UP, pivot on free (right) edge -->
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="75" cy="100" r="2"/>
            <line x1="75" y1="100" x2="75" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateLeft === 'open'">
          <polygon points="7,7 67,16 67,199 7,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: open → LEFT (toward hinge) -->
          <g v-if="showHandleLeft" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="65" cy="107" r="2"/>
            <line x1="65" y1="107" x2="50" y2="107" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="45" y="100" text-anchor="middle" dominant-baseline="middle" font-size="28" fill="currentColor" opacity="0.4">?</text>
        </template>

        <!-- Right wing pane (R-hinged, tuere_r offset +90) -->
        <template v-if="stateRight === 'closed'">
          <rect x="97" y="7" width="76" height="183" stroke-width="2"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: closed → DOWN -->
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="99" cy="100" r="2"/>
            <line x1="99" y1="100" x2="99" y2="115" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateRight === 'tilted'">
          <!-- kipp: bottom fixed, top shifts right 13 units (mirror of left wing) -->
          <polygon points="110,7 186,7 173,190 97,190" stroke-width="2"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: kipp → UP, pivot on free (left) edge -->
          <g v-if="showHandleRight" class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="105" cy="100" r="2"/>
            <line x1="105" y1="100" x2="105" y2="85" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else-if="stateRight === 'open'">
          <polygon points="173,7 113,16 113,199 173,190" stroke-width="2" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <!-- handle: open → RIGHT (toward hinge) -->
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
          <!-- handle on free edge of open panel, same direction (LEFT) — ändert sich nicht -->
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
          <!-- handle on free edge of open panel, same direction (RIGHT) -->
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
          <!-- schiebetuer: movable=right → grab right edge to slide left; schiebetuer_r: movable=left → grab left edge to slide right -->
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
          <!-- handle at right edge of movable panel → DOWN -->
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
          <!-- handle at left edge of movable panel → DOWN -->
          <g class="stroke-gray-500 dark:stroke-gray-400 fill-gray-500 dark:fill-gray-400">
            <circle cx="103" cy="48" r="2"/>
            <line x1="103" y1="48" x2="103" y2="71" stroke-width="3" stroke-linecap="round"/>
          </g>
        </template>
        <template v-else>
          <text x="100" y="50" text-anchor="middle" dominant-baseline="middle" font-size="30" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Roof window (dachfenster) ──────────────────────────────── -->
      <svg
        v-else-if="mode === 'dachfenster'"
        viewBox="0 0 72 56"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame (landscape orientation) -->
        <rect x="2" y="2" width="68" height="52" rx="1" stroke="currentColor" stroke-width="2.5"/>

        <!-- Closed: gray pane fills the frame -->
        <template v-if="roofState === 'closed'">
          <rect x="7" y="7" width="58" height="42" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
        </template>

        <!-- Open / partial: gray pane outline + moving bar showing open amount -->
        <template v-else>
          <rect x="7" y="7" width="58" height="42" stroke-width="1.5"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.4"/>
          <line
            x1="7"
            :y1="7 + (42 * (1 - openPct / 100))"
            x2="65"
            :y2="7 + (42 * (1 - openPct / 100))"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          />
          <rect
            x="7"
            y="7"
            width="58"
            :height="42 * openPct / 100"
            stroke="currentColor"
            stroke-width="1"
            stroke-dasharray="3,3"
            fill="none"
            opacity="0.5"
          />
          <text
            v-if="position !== null && roofState === 'tilted'"
            x="36" y="44"
            text-anchor="middle"
            dominant-baseline="middle"
            font-size="10"
            fill="currentColor"
            opacity="0.8"
          >{{ Math.round(openPct) }}%</text>
        </template>
      </svg>

    </div>
  </div>
</template>
