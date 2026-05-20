<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import { datapoints } from '@/api/client'
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

// ── Konfiguration ─────────────────────────────────────────────────────────────

const label          = computed(() => (props.config.label           as string   | undefined) ?? '')
const color          = computed(() => (props.config.color           as string   | undefined) ?? '#ef4444')
const minTemp        = computed(() => (props.config.min_temp        as number   | undefined) ?? 5)
const maxTemp        = computed(() => (props.config.max_temp        as number   | undefined) ?? 35)
const step           = computed(() => (props.config.step            as number   | undefined) ?? 0.5)
const decimals       = computed(() => (props.config.decimals        as number   | undefined) ?? 1)
const setpointOffset = computed(() => (props.config.setpoint_offset as number   | undefined) ?? 0)
const actualOffset   = computed(() => (props.config.actual_offset   as number   | undefined) ?? 0)
const actualDpId     = computed(() => (props.config.actual_temp_dp_id as string | null | undefined) ?? null)
const modeDpId       = computed(() => (props.config.mode_dp_id     as string   | null | undefined) ?? null)
const showModes      = computed(() => (props.config.show_modes      as boolean  | undefined) ?? true)
const supportedModes = computed(() => (props.config.supported_modes as number[] | undefined) ?? [0, 1, 2, 3, 4])
const variant        = computed(() => (props.config.variant         as 'heating' | 'ac' | undefined) ?? 'heating')

// ── Solltemperatur ────────────────────────────────────────────────────────────

function toNum(v: DataPointValue | null, fallback: number): number {
  if (!v) return fallback
  const n = typeof v.v === 'number' ? v.v : parseFloat(String(v.v))
  return isNaN(n) ? fallback : n
}

// statusValue = KNX-Rückmeldung hat Vorrang gegenüber dem Schreib-DP
const rawSetpoint  = computed(() => props.statusValue ?? props.value)
const baseSetpoint = computed(() => toNum(rawSetpoint.value, 20))

const pendingSetpoint = ref<number | null>(null)
let pendingTimer: ReturnType<typeof setTimeout> | null = null

function clearPending() {
  pendingSetpoint.value = null
  if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null }
}
watch(rawSetpoint, clearPending)

const shownSetpoint   = computed(() =>
  pendingSetpoint.value !== null ? pendingSetpoint.value : baseSetpoint.value,
)
const displaySetpoint = computed(() =>
  (shownSetpoint.value + setpointOffset.value).toFixed(decimals.value),
)

async function adjustSetpoint(delta: number) {
  if (props.editorMode || props.readonly || !props.datapointId) return
  const raw      = shownSetpoint.value + delta
  const quantized = Math.round(raw / step.value) * step.value
  const next     = parseFloat(
    Math.max(minTemp.value, Math.min(maxTemp.value, quantized)).toFixed(10),
  )
  pendingSetpoint.value = next
  pendingTimer = setTimeout(clearPending, 5000)
  try {
    await datapoints.write(props.datapointId, next)
  } catch {
    clearPending()
  }
}

// ── Isttemperatur (extra DP) ──────────────────────────────────────────────────

const actualDp     = computed(() => actualDpId.value ? dpStore.getValue(actualDpId.value) : null)
const hasActual    = computed(() => !!actualDpId.value)
const actualNumVal = computed(() =>
  actualDp.value !== null ? toNum(actualDp.value, 20) + actualOffset.value : null,
)
const displayActual = computed(() => {
  if (!hasActual.value) return null
  if (actualDp.value === null) return '…'
  return actualNumVal.value!.toFixed(decimals.value)
})

// ── Betriebsart (extra DP) ────────────────────────────────────────────────────

const modeRawDp   = computed(() => modeDpId.value ? dpStore.getValue(modeDpId.value) : null)
const currentMode = computed<number>(() => {
  const dp = modeRawDp.value
  if (!dp) return -1
  const n = typeof dp.v === 'number' ? dp.v : parseInt(String(dp.v), 10)
  return isNaN(n) ? -1 : n
})

async function setMode(mode: number) {
  if (props.editorMode || props.readonly || !modeDpId.value) return
  try {
    await datapoints.write(modeDpId.value, mode)
  } catch { /* ignore */ }
}

const HEATING_MODES = [
  { value: 0, label: 'Auto'        },
  { value: 1, label: 'Komfort'     },
  { value: 2, label: 'Standby'     },
  { value: 3, label: 'Economy'     },
  { value: 4, label: 'Frostschutz' },
]

const AC_MODES = [
  { value:  0, label: 'Automatik'   },
  { value:  1, label: 'Heizen'      },
  { value:  3, label: 'Kühlen'      },
  { value:  6, label: 'Aus'         },
  { value:  9, label: 'Nur Lüfter'  },
  { value: 14, label: 'Entfeuchten' },
]

const allModes         = computed(() => variant.value === 'ac' ? AC_MODES : HEATING_MODES)
const visibleModes     = computed(() => allModes.value.filter(m => supportedModes.value.includes(m.value)))
const currentModeLabel = computed(() => allModes.value.find(m => m.value === currentMode.value)?.label ?? null)

// ── Einheit ───────────────────────────────────────────────────────────────────

const unit = computed(() => rawSetpoint.value?.u ?? '°C')

// ── SVG-Arc-Geometrie ─────────────────────────────────────────────────────────
// ViewBox 0 0 100 100 · Mittelpunkt (50, 46) · Radius 36
// Start 135° (7 Uhr / SW) → im Uhrzeigersinn 270° → Ende 45° (5 Uhr / SE)
// Gap am unteren Rand – klassisches Thermostat-Layout

const CX        = 50
const CY        = 46
const R         = 36
const START_DEG = 135
const SWEEP_DEG = 270

function arcPoint(deg: number) {
  const rad = (deg * Math.PI) / 180
  return { x: CX + R * Math.cos(rad), y: CY + R * Math.sin(rad) }
}

const arcStart = arcPoint(START_DEG)
const arcEnd   = arcPoint(START_DEG + SWEEP_DEG)

// Hintergrund-Arc (voller Bereich)
const bgArc = `M ${arcStart.x.toFixed(2)} ${arcStart.y.toFixed(2)} A ${R} ${R} 0 1 1 ${arcEnd.x.toFixed(2)} ${arcEnd.y.toFixed(2)}`

function valueToAngle(val: number) {
  const clamped = Math.max(minTemp.value, Math.min(maxTemp.value, val))
  return START_DEG + ((clamped - minTemp.value) / (maxTemp.value - minTemp.value)) * SWEEP_DEG
}

// Farbiger Arc bis zum Sollwert
const setpointArc = computed(() => {
  const angleDeg = valueToAngle(shownSetpoint.value)
  const sweep    = angleDeg - START_DEG
  if (sweep < 1) return ''
  const end = arcPoint(angleDeg)
  return `M ${arcStart.x.toFixed(2)} ${arcStart.y.toFixed(2)} A ${R} ${R} 0 ${sweep > 180 ? 1 : 0} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`
})

const setpointTip   = computed(() => arcPoint(valueToAngle(shownSetpoint.value)))

// Weißer Ring als Isttemperatur-Markierung
const actualMarker = computed(() => {
  if (!hasActual.value || actualDp.value === null || actualNumVal.value === null) return null
  return arcPoint(valueToAngle(actualNumVal.value))
})
</script>

<template>
  <div
    class="h-full w-full flex flex-col items-center select-none overflow-hidden py-2 px-1"
    data-testid="rtr-widget"
  >
    <!-- Beschriftung -->
    <p
      v-if="label"
      class="text-xs text-gray-500 dark:text-gray-400 truncate text-center w-full mb-1 shrink-0"
    >{{ label }}</p>

    <!-- Arc-Gauge (SVG) -->
    <div class="flex-1 min-h-0 w-full">
      <svg
        viewBox="0 0 100 100"
        class="w-full h-full"
        preserveAspectRatio="xMidYMid meet"
        xmlns="http://www.w3.org/2000/svg"
        data-testid="rtr-arc"
      >
        <!-- Hintergrund-Arc (voller Bereich) -->
        <path
          :d="bgArc"
          fill="none"
          class="stroke-gray-200 dark:stroke-gray-700"
          stroke-width="6"
          stroke-linecap="round"
        />

        <!-- Soll-Arc (eingefärbt) -->
        <path
          v-if="setpointArc"
          :d="setpointArc"
          fill="none"
          :stroke="color"
          stroke-width="6"
          stroke-linecap="round"
          data-testid="rtr-setpoint-arc"
        />

        <!-- Sollwert-Endmarke (gefüllter Kreis) -->
        <circle
          v-if="setpointArc"
          :cx="setpointTip.x"
          :cy="setpointTip.y"
          r="5"
          :fill="color"
        />

        <!-- Isttemperatur-Markierung (weißer Ring) -->
        <circle
          v-if="actualMarker"
          :cx="actualMarker.x"
          :cy="actualMarker.y"
          r="4"
          fill="white"
          :stroke="color"
          stroke-width="2"
          opacity="0.85"
          data-testid="rtr-actual-marker"
        />

        <!-- Min/Max-Beschriftung an den Arc-Enden -->
        <text
          :x="arcStart.x - 2"
          :y="arcStart.y + 9"
          font-size="5"
          text-anchor="end"
          fill="#9ca3af"
        >{{ minTemp }}°</text>
        <text
          :x="arcEnd.x + 2"
          :y="arcEnd.y + 9"
          font-size="5"
          text-anchor="start"
          fill="#9ca3af"
        >{{ maxTemp }}°</text>

        <!-- Solltemperatur (groß, Mitte) -->
        <text
          x="50"
          y="41"
          font-size="15"
          font-weight="700"
          text-anchor="middle"
          dominant-baseline="auto"
          :fill="color"
          data-testid="rtr-setpoint-value"
        >{{ displaySetpoint }}</text>

        <!-- Einheit + "Soll" -->
        <text
          x="50"
          y="50"
          font-size="5.5"
          text-anchor="middle"
          dominant-baseline="auto"
          fill="#9ca3af"
        >{{ unit }} Soll</text>

        <!-- Isttemperatur (falls konfiguriert) -->
        <text
          v-if="hasActual"
          x="50"
          y="62"
          font-size="6.5"
          text-anchor="middle"
          dominant-baseline="auto"
          fill="#6b7280"
          data-testid="rtr-actual-value"
        >Ist: {{ displayActual }} {{ unit }}</text>

        <!-- Aktive Betriebsart (falls DP konfiguriert und Wert bekannt) -->
        <text
          v-if="modeDpId && currentModeLabel"
          x="50"
          :y="hasActual ? '73' : '62'"
          font-size="5"
          text-anchor="middle"
          dominant-baseline="auto"
          fill="#6b7280"
          data-testid="rtr-mode-label"
        >{{ currentModeLabel }}</text>
      </svg>
    </div>

    <!-- +/- Buttons -->
    <div class="flex items-center justify-center gap-3 shrink-0 mt-1">
      <button
        class="w-8 h-8 rounded-full border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 text-lg font-bold flex items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-40 disabled:cursor-default leading-none"
        :disabled="editorMode || readonly || !datapointId"
        data-testid="rtr-btn-minus"
        @click="adjustSetpoint(-step)"
      >−</button>
      <span
        class="text-xs text-gray-400 dark:text-gray-500 tabular-nums w-16 text-center"
        data-testid="rtr-step-display"
      >{{ displaySetpoint }} {{ unit }}</span>
      <button
        class="w-8 h-8 rounded-full border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 text-lg font-bold flex items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-40 disabled:cursor-default leading-none"
        :disabled="editorMode || readonly || !datapointId"
        data-testid="rtr-btn-plus"
        @click="adjustSetpoint(+step)"
      >+</button>
    </div>

    <!-- Betriebsart-Buttons -->
    <div
      v-if="showModes && visibleModes.length && modeDpId"
      class="flex gap-1 shrink-0 mt-1 px-1 flex-wrap justify-center"
      data-testid="rtr-mode-buttons"
    >
      <button
        v-for="m in visibleModes"
        :key="m.value"
        :data-testid="`rtr-mode-btn-${m.value}`"
        :class="[
          'text-xs px-2 py-0.5 rounded-full border transition-colors',
          currentMode === m.value
            ? 'text-white border-transparent'
            : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:border-gray-500',
        ]"
        :style="currentMode === m.value ? { backgroundColor: color } : {}"
        :disabled="editorMode || readonly"
        @click="setMode(m.value)"
      >{{ m.label }}</button>
    </div>

  </div>
</template>
