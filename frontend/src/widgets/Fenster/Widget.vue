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
      <!--
        viewBox 56×64  |  outer frame: rect(2,2,52,60)
        inner pane area: x 7→49 (w=42), y 7→57 (h=50)
        KNX reference analysis (361px viewBox):
          kipp : bottom fixed (103→266), top shifted LEFT ~17% of width → top: 75→233
          open : hinge left x≈101, free right x≈233 (79% of frame w),
                 free side falls ~5px lower (perspective), bottom-right exits frame
      -->
      <svg
        v-if="mode === 'fenster'"
        viewBox="0 0 56 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="52" height="60" rx="1" stroke-width="2.5" stroke="currentColor"/>

        <!-- Closed: inner pane (gray wing, colored frame) + handle dot -->
        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="42" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="47" cy="32" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Tilted (Kipp): gray parallelogram — bottom fixed, top shifted left ~7px -->
        <template v-else-if="stateMain === 'tilted'">
          <polygon points="0,7 42,7 49,57 7,57" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="44" cy="32" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Open: gray perspective parallelogram as polygon (fills over frame edge) -->
        <template v-else-if="stateMain === 'open'">
          <polygon points="7,7 40,12 40,62 7,57" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="38" cy="37" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Single-wing window RIGHT-hinged (fenster_r) ──────────────── -->
      <!--
        Mirror of fenster: hinge right x=49, free side x=16 (49-33)
        Kipp: same parallelogram (kipp is bottom-pivoted, direction-agnostic in KNX standard)
        Open: perspective falls identically — free side 5px lower, free top (16,12), free bottom (16,62)
      -->
      <svg
        v-else-if="mode === 'fenster_r'"
        viewBox="0 0 56 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- Outer frame -->
        <rect x="2" y="2" width="52" height="60" rx="1" stroke-width="2.5" stroke="currentColor"/>

        <!-- Closed: inner pane (gray wing) + handle dot left-centre -->
        <template v-if="stateMain === 'closed'">
          <rect x="7" y="7" width="42" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="9" cy="32" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Tilted (Kipp): gray parallelogram -->
        <template v-else-if="stateMain === 'tilted'">
          <polygon points="0,7 42,7 49,57 7,57" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="12" cy="32" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Open: gray perspective parallelogram, hinge right x=49, free side x=16 -->
        <template v-else-if="stateMain === 'open'">
          <polygon points="49,7 16,12 16,62 49,57" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="18" cy="37" r="2"
                  class="fill-gray-500 dark:fill-gray-400"/>
        </template>

        <!-- Unknown -->
        <template v-else>
          <text x="28" y="37" text-anchor="middle" dominant-baseline="middle" font-size="20" fill="currentColor" opacity="0.4">?</text>
        </template>
      </svg>

      <!-- ── Double-wing window (fenster_2) ──────────────────────────── -->
      <!--
        viewBox 72×64  |  outer frame: x 2→70, y 2→62  |  center divider x=36
        Left wing  inner pane: x 7→31 (w=24), y 7→57 (h=50)
        Right wing inner pane: x 41→65 (w=24), y 7→57 (h=50)
        Same proportions as single-wing:
          kipp : 17% of 24 = ~4px left shift  |  open : 79% of 24 = 19px, falls 3px
        Frame is split L/R so each half shows its wing's state color (matching single-wing principle).
        Panes are fully gray (fill + stroke), identical to single-wing.
      -->
      <svg
        v-else-if="mode === 'fenster_2'"
        viewBox="0 0 72 64"
        class="w-full h-full max-h-full"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <!-- ① Left half-frame: state color of left wing -->
        <g :class="stateColorClass(stateLeft)">
          <line x1="2"  y1="2"  x2="2"  y2="62" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="2"  y1="2"  x2="36" y2="2"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="2"  y1="62" x2="36" y2="62" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>

        <!-- ② Right half-frame: state color of right wing -->
        <g :class="stateColorClass(stateRight)">
          <line x1="70" y1="2"  x2="70" y2="62" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="36" y1="2"  x2="70" y2="2"  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="36" y1="62" x2="70" y2="62" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        </g>

        <!-- ③ Center divider: summaryState (worst-case of both wings) -->
        <line x1="36" y1="2" x2="36" y2="62" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>

        <!-- ④ Left wing pane — gray fill + gray stroke, drawn AFTER frame so it sits in front -->
        <template v-if="stateLeft === 'closed'">
          <rect x="7" y="7" width="24" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="29" cy="32" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else-if="stateLeft === 'tilted'">
          <!-- kipp: bottom (7→31,57) fixed, top shifted left 4px → (3→27,7) -->
          <polygon points="3,7 27,7 31,57 7,57" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="28" cy="32" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else-if="stateLeft === 'open'">
          <!-- open: hinge x=7, free x=26 (79% of 24), falls +3px; bottom-right at y=60 -->
          <polygon points="7,7 26,10 26,60 7,57" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="24" cy="35" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else>
          <text x="19" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14"
                class="fill-gray-400 dark:fill-gray-500" opacity="0.6">?</text>
        </template>

        <!-- ⑤ Right wing pane — mirror of left wing -->
        <template v-if="stateRight === 'closed'">
          <rect x="41" y="7" width="24" height="50" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="43" cy="32" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else-if="stateRight === 'tilted'">
          <!-- kipp: bottom (41→65,57) fixed, top shifted left 4px → (37→61,7) -->
          <polygon points="37,7 61,7 65,57 41,57" stroke-width="1.5"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="44" cy="32" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else-if="stateRight === 'open'">
          <!-- open: hinge x=65, free x=46 (79% of 24 from right), falls +3px -->
          <polygon points="65,7 46,10 46,60 65,57" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <circle cx="48" cy="35" r="2" class="fill-gray-500 dark:fill-gray-400"/>
        </template>
        <template v-else>
          <text x="53" y="37" text-anchor="middle" dominant-baseline="middle" font-size="14"
                class="fill-gray-400 dark:fill-gray-500" opacity="0.6">?</text>
        </template>
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

        <!-- Closed: gray door panel -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="5" width="44" height="64" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
        </template>

        <!-- Open: gray perspective parallelogram, hinge left x=6, free side x=28 -->
        <template v-else-if="stateMain === 'open'">
          <polygon points="6,5 28,8 28,70 6,69" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
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

        <!-- Closed: gray door panel -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="5" width="44" height="64" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
        </template>

        <!-- Open: gray perspective parallelogram, hinge right x=50, free side x=28 -->
        <template v-else-if="stateMain === 'open'">
          <polygon points="50,5 28,8 28,70 50,69" stroke-width="1.5" stroke-linejoin="round"
                   class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
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
        <!-- Floor line: same style as Türe (thin, semi-transparent) -->
        <line x1="2"  y1="60" x2="70" y2="60" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.3"/>

        <!-- Closed: full gray panel -->
        <template v-if="stateMain === 'closed'">
          <rect x="6" y="8" width="60" height="48" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
        </template>

        <!-- Open, fixer Teil LINKS: moving panel (gray) slid left, ghost outline for gap -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer'">
          <rect x="6"  y="8" width="28" height="48" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
          <rect x="38" y="8" width="28" height="48" stroke-width="1" stroke-dasharray="3,3"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
        </template>

        <!-- Open, fixer Teil RECHTS: ghost on left, moving panel (gray) on right -->
        <template v-else-if="stateMain === 'open' && mode === 'schiebetuer_r'">
          <rect x="6"  y="8" width="28" height="48" stroke-width="1" stroke-dasharray="3,3"
                class="fill-gray-200 dark:fill-gray-700 stroke-gray-300 dark:stroke-gray-600" opacity="0.5"/>
          <rect x="38" y="8" width="28" height="48" stroke-width="1.5"
                class="fill-gray-300 dark:fill-gray-600 stroke-gray-400 dark:stroke-gray-500"/>
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
