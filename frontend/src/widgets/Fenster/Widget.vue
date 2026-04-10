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
    if (pos <= 0)  return 'closed'
    if (pos >= 100) return 'open'
    return 'tilted' // partially open
  }
  return deriveState(dpContact.value, invContact.value, dpTilt.value, invTilt.value)
})

function stateLabel(s: WinState): string {
  switch (s) {
    case 'closed':  return 'Geschlossen'
    case 'tilted':  return 'Gekippt'
    case 'open':    return 'Offen'
    default:        return '—'
  }
}

const stateText = computed(() => {
  if (mode.value === 'fenster_2') {
    return `L: ${stateLabel(stateLeft.value)} / R: ${stateLabel(stateRight.value)}`
  }
  if (mode.value === 'dachfenster') {
    const pos = position.value
    if (pos !== null && pos > 0 && pos < 100) return `${Math.round(pos)} % offen`
    return stateLabel(roofState.value)
  }
  return stateLabel(stateMain.value)
})

function stateColorClass(s: WinState): string {
  switch (s) {
    case 'closed':  return 'text-green-600 dark:text-green-400'
    case 'tilted':  return 'text-amber-500 dark:text-amber-400'
    case 'open':    return 'text-blue-500 dark:text-blue-400'
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
    if (roofState.value === 'open') return 100
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

      <!-- ── Single-wing window (fenster) ────────────────────────── -->
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

        <!-- Tilted (Kipp): V from top-center to bottom corners -->
        <template v-else-if="stateMain === 'tilted'">
          <rect x="7" y="7" width="42" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.3"/>
          <line x1="28" y1="8" x2="8" y2="56" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="28" y1="8" x2="48" y2="56" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="8" y1="56" x2="48" y2="56" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </template>

        <!-- Open: wing swung to the right -->
        <template v-else-if="stateMain === 'open'">
          <!-- hinge side (left vertical) -->
          <line x1="8" y1="7" x2="8" y2="57" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <!-- open pane as parallelogram -->
          <line x1="8" y1="7"  x2="38" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="8" y1="57" x2="38" y2="50" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="38" y1="14" x2="38" y2="50" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Double-wing window (fenster_2) ──────────────────────── -->
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

        <!-- Left wing -->
        <g :class="stateColorClass(stateLeft)">
          <template v-if="stateLeft === 'closed'">
            <rect x="7" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
          </template>
          <template v-else-if="stateLeft === 'tilted'">
            <rect x="7" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.3"/>
            <line x1="19" y1="8" x2="8" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="19" y1="8" x2="30" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="8"  y1="56" x2="30" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else-if="stateLeft === 'open'">
            <line x1="34" y1="8"  x2="34" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="34" y1="8"  x2="10" y2="14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="34" y1="57" x2="10" y2="50" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="10" y1="14" x2="10" y2="50" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else>
            <text x="19" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="currentColor" opacity="0.4">?</text>
          </template>
        </g>

        <!-- Right wing -->
        <g :class="stateColorClass(stateRight)">
          <template v-if="stateRight === 'closed'">
            <rect x="41" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.5"/>
          </template>
          <template v-else-if="stateRight === 'tilted'">
            <rect x="41" y="7" width="24" height="50" stroke-width="1.5" stroke="currentColor" fill="none" opacity="0.3"/>
            <line x1="53" y1="8" x2="42" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="53" y1="8" x2="64" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="42" y1="56" x2="64" y2="56" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else-if="stateRight === 'open'">
            <line x1="38" y1="8"  x2="38" y2="57" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="38" y1="8"  x2="62" y2="14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="38" y1="57" x2="62" y2="50" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="62" y1="14" x2="62" y2="50" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </template>
          <template v-else>
            <text x="53" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="currentColor" opacity="0.4">?</text>
          </template>
        </g>
      </svg>

      <!-- ── Door (tuere) ─────────────────────────────────────────── -->
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

        <!-- Open: door swung open (arc + panel line) -->
        <template v-else-if="stateMain === 'open'">
          <!-- Door panel shown at ~70° open (from left hinge) -->
          <line x1="6" y1="5" x2="6" y2="69" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.5"/>
          <line x1="6" y1="5" x2="46" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          <!-- Arc showing swing path -->
          <path
            d="M 6 69 A 64 64 0 0 0 46 18"
            stroke="currentColor"
            stroke-width="1"
            stroke-dasharray="3,3"
            opacity="0.5"
          />
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="40" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Sliding door (schiebetuer) ───────────────────────────── -->
      <svg
        v-else-if="mode === 'schiebetuer'"
        viewBox="0 0 72 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame (tracks) -->
        <line x1="2"  y1="4"  x2="2"  y2="60" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="70" y1="4"  x2="70" y2="60" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="2"  y1="4"  x2="70" y2="4"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        <line x1="2"  y1="60" x2="70" y2="60" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>

        <!-- Closed: panel fills the opening -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="8" width="60" height="48" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.5"/>
        </template>

        <!-- Open: panel shifted to the left side -->
        <template v-else-if="stateMain === 'open'">
          <rect x="6" y="8" width="28" height="48" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.7"/>
          <!-- Opening gap (dashed) -->
          <rect x="38" y="8" width="28" height="48" stroke="currentColor" stroke-width="1" stroke-dasharray="3,3" fill="none" opacity="0.3"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="36" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Roof window (dachfenster) ────────────────────────────── -->
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
          <!-- Gap at top based on openPct -->
          <rect x="7" y="7" width="58" height="42" stroke="currentColor" stroke-width="1.5" fill="none" opacity="0.2"/>
          <!-- Open pane: lower part stays, upper opens outward -->
          <!-- Gap indicator: lines at top showing open amount -->
          <line
            x1="7"
            :y1="7 + (42 * (1 - openPct / 100))"
            x2="65"
            :y2="7 + (42 * (1 - openPct / 100))"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          />
          <!-- Top opening gap (dashed outline) -->
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
          <!-- Percentage text -->
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
