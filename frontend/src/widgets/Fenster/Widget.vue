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
  if (mode.value === 'fenster_2') {
    if (stateLeft.value === 'open'   || stateRight.value === 'open')   return 'open'
    if (stateLeft.value === 'tilted' || stateRight.value === 'tilted') return 'tilted'
    if (stateLeft.value === 'closed' && stateRight.value === 'closed') return 'closed'
    return 'unknown'
  }
  if (mode.value === 'dachfenster') return roofState.value
  return stateMain.value
})

const colorClass = computed(() => stateColorClass(summaryState.value))

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
      <svg
        v-if="mode === 'fenster'"
        viewBox="0 0 56 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="52" height="60" rx="1" stroke-width="2.5" stroke="currentColor"/>

        <!-- Closed: inner pane -->
        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="42" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
        </template>

        <!-- Tilted (Kipp): parallelogram, bottom edge fixed, top edge shifted left -->
        <template v-else-if="stateMain === 'tilted'">
          <polygon points="2,7 44,7 49,57 7,57" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.7"/>
        </template>

        <!-- Open: foreshortened panel, hinge left x=7, free side at x=30 -->
        <template v-else-if="stateMain === 'open'">
          <line x1="7" y1="7" x2="7" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="7" y1="7" x2="30" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="7" y1="57" x2="30" y2="53" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="30" y1="11" x2="30" y2="53" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Single-wing window RIGHT-hinged (fenster_r) ──────────────── -->
      <svg
        v-else-if="mode === 'fenster_r'"
        viewBox="0 0 56 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="52" height="60" rx="1" stroke-width="2.5" stroke="currentColor"/>

        <!-- Closed: inner pane -->
        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="42" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
        </template>

        <!-- Tilted (Kipp): parallelogram, bottom edge fixed, top edge shifted left -->
        <template v-else-if="stateMain === 'tilted'">
          <polygon points="2,7 44,7 49,57 7,57" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.7"/>
        </template>

        <!-- Open: foreshortened panel, hinge right x=49, free side at x=26 -->
        <template v-else-if="stateMain === 'open'">
          <line x1="49" y1="7" x2="49" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="49" y1="7" x2="26" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="49" y1="57" x2="26" y2="53" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="26" y1="11" x2="26" y2="53" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Double-wing window (fenster_2) ──────────────────────────── -->
      <svg
        v-else-if="mode === 'fenster_2'"
        viewBox="0 0 72 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="68" height="60" rx="1" stroke-width="2.5" stroke="currentColor"/>
        <!-- Center divider -->
        <line x1="36" y1="2" x2="36" y2="62" stroke-width="2.5" stroke="currentColor"/>

        <!-- Left wing (hinge at left outer frame x=7, free side at center x=31) -->
        <g :class="stateColorClass(stateLeft)">
          <template v-if="stateLeft === 'closed'">
            <rect x="7" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
          </template>
          <template v-else-if="stateLeft === 'tilted'">
            <polygon points="4,7 28,7 31,57 7,57" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.7"/>
          </template>
          <template v-else-if="stateLeft === 'open'">
            <line x1="7" y1="8" x2="7" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="7" y1="8" x2="21" y2="10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="7" y1="57" x2="21" y2="55" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="21" y1="10" x2="21" y2="55" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else>
            <text x="19" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="currentColor" opacity="0.4">?</text>
          </template>
        </g>

        <!-- Right wing (hinge at right outer frame x=65, free side at center x=41) -->
        <g :class="stateColorClass(stateRight)">
          <template v-if="stateRight === 'closed'">
            <rect x="41" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
          </template>
          <template v-else-if="stateRight === 'tilted'">
            <polygon points="38,7 62,7 65,57 41,57" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.7"/>
          </template>
          <template v-else-if="stateRight === 'open'">
            <line x1="65" y1="8" x2="65" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="65" y1="8" x2="51" y2="10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="65" y1="57" x2="51" y2="55" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="51" y1="10" x2="51" y2="55" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else>
            <text x="53" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="currentColor" opacity="0.4">?</text>
          </template>
        </g>
      </svg>

      <!-- ── Door LEFT-hinged (tuere) ──────────────────────────────────── -->
      <svg
        v-else-if="mode === 'tuere'"
        viewBox="0 0 56 72"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Door frame -->
        <line x1="2"  y1="2"  x2="2"  y2="70" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="54" y1="2"  x2="54" y2="70" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="2"  y1="2"  x2="54" y2="2"  stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <!-- Floor line -->
        <line x1="2"  y1="70" x2="54" y2="70" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <!-- Closed: door panel -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="5" width="44" height="64" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.5"/>
        </template>

        <!-- Open: foreshortened panel, hinge left x=6, free side at x=28 -->
        <template v-else-if="stateMain === 'open'">
          <line x1="6"  y1="5"  x2="6"  y2="69" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="6"  y1="5"  x2="28" y2="8"  stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="6"  y1="69" x2="28" y2="66" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="28" y1="8"  x2="28" y2="66" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="40" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Door RIGHT-hinged (tuere_r) ──────────────────────────────── -->
      <svg
        v-else-if="mode === 'tuere_r'"
        viewBox="0 0 56 72"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Door frame -->
        <line x1="2"  y1="2"  x2="2"  y2="70" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="54" y1="2"  x2="54" y2="70" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="2"  y1="2"  x2="54" y2="2"  stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <!-- Floor line -->
        <line x1="2"  y1="70" x2="54" y2="70" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <!-- Closed: door panel -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="5" width="44" height="64" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.5"/>
        </template>

        <!-- Open: foreshortened panel, hinge right x=50, free side at x=28 -->
        <template v-else-if="stateMain === 'open'">
          <line x1="50" y1="5"  x2="50" y2="69" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="50" y1="5"  x2="28" y2="8"  stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="50" y1="69" x2="28" y2="66" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <line x1="28" y1="8"  x2="28" y2="66" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="40" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Sliding door, fixed part LEFT (schiebetuer) ──────────────── -->
      <svg
        v-else-if="mode === 'schiebetuer' || mode === 'schiebetuer_r'"
        viewBox="0 0 72 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <line x1="2"  y1="4"  x2="2"  y2="60" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="70" y1="4"  x2="70" y2="60" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="2"  y1="4"  x2="70" y2="4"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        <line x1="2"  y1="60" x2="70" y2="60" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>

        <!-- Closed: full panel (same for both variants) -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="8" width="60" height="48" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.5"/>
        </template>

        <!-- Open, fixer Teil LINKS: panel slides left, gap on right -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer'">
          <rect x="6"  y="8" width="28" height="48" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.7"/>
          <rect x="38" y="8" width="28" height="48" stroke="currentColor" stroke-width="1" stroke-dasharray="3,3" fill="none" opacity="0.3"/>
        </template>

        <!-- Open, fixer Teil RECHTS: gap on left, panel slides right -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer_r'">
          <rect x="6"  y="8" width="28" height="48" stroke="currentColor" stroke-width="1" stroke-dasharray="3,3" fill="none" opacity="0.3"/>
          <rect x="38" y="8" width="28" height="48" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.7"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="36" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
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

        <!-- Closed: pane fills the frame -->
        <template v-if="roofState === 'closed'">
          <rect x="7" y="7" width="58" height="42" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.5"/>
        </template>

        <!-- Open / partial: pane hinged at bottom, top gap -->
        <template v-else>
          <rect x="7" y="7" width="58" height="42" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.2"/>
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
