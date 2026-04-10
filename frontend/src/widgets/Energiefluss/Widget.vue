<script setup lang="ts">
import { computed } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'

type FlowDirection = 'to_house' | 'from_house' | 'bidirectional'

interface EntityConfig {
  id: string
  label: string
  icon: string
  color: string
  direction: FlowDirection
  unit: string
  decimals: number
  invert: boolean
}

interface Point { x: number; y: number }

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: null
  editorMode: boolean
  w?: number
  h?: number
}>()

const dpStore = useDatapointsStore()

// ── Config ────────────────────────────────────────────────────────────────────

const widgetLabel  = computed(() => (props.config.label    as string | undefined) ?? '')
const houseDpId    = computed(() => (props.config.house_dp as string | undefined) ?? '')
const houseUnit    = computed(() => (props.config.house_unit as string | undefined) ?? 'W')
const houseDec     = computed(() => (props.config.house_decimals as number | undefined) ?? 1)

const entities = computed<EntityConfig[]>(() => {
  const raw = (props.config.entities as Partial<EntityConfig>[] | undefined) ?? []
  return raw.filter((e) => !!e.id).map((e) => ({
    id: e.id!,
    label: e.label ?? '',
    icon: e.icon ?? '⚡',
    color: e.color ?? '#60a5fa',
    direction: e.direction ?? 'bidirectional',
    unit: e.unit ?? 'W',
    decimals: e.decimals ?? 1,
    invert: e.invert ?? false,
  }))
})

// ── Values ────────────────────────────────────────────────────────────────────

function formatPower(watts: number, unit: string, decimals: number): string {
  const u = unit || 'W'
  if ((u === 'W' || u === 'Watt') && Math.abs(watts) >= 1000) {
    return (watts / 1000).toFixed(Math.max(1, decimals)) + '\u202FkW'
  }
  return watts.toFixed(decimals) + '\u202F' + u
}

/** isSource: true = Energie fliesst zum Haus (Punkt von Knoten → Mitte) */
function resolveIsSource(power: number, direction: FlowDirection): boolean {
  if (direction === 'to_house')   return true
  if (direction === 'from_house') return false
  // bidirectional: positiver Wert → Quelle (Richtung Haus)
  return power >= 0
}

interface EntityDisplay {
  label: string
  icon: string
  color: string
  power: number
  displayValue: string
  active: boolean
  isSource: boolean
}

const displays = computed<EntityDisplay[]>(() =>
  entities.value.map((e): EntityDisplay => {
    if (props.editorMode) {
      return {
        label: e.label || e.id,
        icon: e.icon,
        color: e.color,
        power: 0,
        displayValue: '—',
        active: false,
        isSource: resolveIsSource(0, e.direction),
      }
    }
    const dp = dpStore.getValue(e.id)
    if (dp === null) {
      return {
        label: e.label || e.id,
        icon: e.icon,
        color: e.color,
        power: 0,
        displayValue: '…',
        active: false,
        isSource: resolveIsSource(0, e.direction),
      }
    }
    const raw = typeof dp.v === 'number' ? dp.v : parseFloat(String(dp.v))
    const power = isNaN(raw) ? 0 : (e.invert ? -raw : raw)
    const unit = dp.u ?? e.unit
    return {
      label: e.label || e.id,
      icon: e.icon,
      color: e.color,
      power,
      displayValue: formatPower(power, unit, e.decimals),
      active: Math.abs(power) > 1,
      isSource: resolveIsSource(power, e.direction),
    }
  })
)

// ── SVG Layout ────────────────────────────────────────────────────────────────

const VB = 280
const CX = 140
const CY = 140
const ENTITY_RADIUS = 88
const ENTITY_R = 16

function entityPos(i: number, total: number): Point {
  const angle = (i / total) * 2 * Math.PI - Math.PI / 2
  return {
    x: CX + ENTITY_RADIUS * Math.cos(angle),
    y: CY + ENTITY_RADIUS * Math.sin(angle),
  }
}

const positions = computed<Point[]>(() =>
  displays.value.map((_, i) => entityPos(i, displays.value.length))
)

type TextSide = 'above' | 'below' | 'left' | 'right'

function textSide(pos: Point): TextSide {
  const dx = pos.x - CX
  const dy = pos.y - CY
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? 'right' : 'left'
  return dy > 0 ? 'below' : 'above'
}

interface TextLayout {
  labelX: number
  labelY: number
  valueX: number
  valueY: number
  anchor: string
}

const TEXT_GAP = 7

function getTextLayout(pos: Point): TextLayout {
  const side = textSide(pos)
  switch (side) {
    case 'above':
      return { labelX: pos.x, labelY: pos.y - ENTITY_R - TEXT_GAP - 9,
               valueX: pos.x, valueY: pos.y - ENTITY_R - TEXT_GAP, anchor: 'middle' }
    case 'below':
      return { labelX: pos.x, labelY: pos.y + ENTITY_R + TEXT_GAP + 1,
               valueX: pos.x, valueY: pos.y + ENTITY_R + TEXT_GAP + 10, anchor: 'middle' }
    case 'right':
      return { labelX: pos.x + ENTITY_R + TEXT_GAP, labelY: pos.y - 4,
               valueX: pos.x + ENTITY_R + TEXT_GAP, valueY: pos.y + 6, anchor: 'start' }
    case 'left':
      return { labelX: pos.x - ENTITY_R - TEXT_GAP, labelY: pos.y - 4,
               valueX: pos.x - ENTITY_R - TEXT_GAP, valueY: pos.y + 6, anchor: 'end' }
  }
}

// ── Hausverbrauch ─────────────────────────────────────────────────────────────

const houseDisplay = computed<string>(() => {
  if (props.editorMode) return '—'
  if (!houseDpId.value) return ''
  const dp = dpStore.getValue(houseDpId.value)
  if (dp === null) return '…'
  const raw = typeof dp.v === 'number' ? dp.v : parseFloat(String(dp.v))
  if (isNaN(raw)) return '—'
  return formatPower(raw, dp.u ?? houseUnit.value, houseDec.value)
})

// Animation speed: faster at higher power
function animDur(power: number): string {
  const s = Math.max(0.6, Math.min(5, 4000 / Math.max(50, Math.abs(power))))
  return `${s.toFixed(2)}s`
}
</script>

<template>
  <div class="flex flex-col w-full h-full select-none overflow-hidden">
    <p
      v-if="widgetLabel"
      class="text-xs text-center text-gray-500 dark:text-gray-400 pt-1 px-2 truncate shrink-0"
    >
      {{ widgetLabel }}
    </p>

    <div
      v-if="displays.length === 0"
      class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500 text-xs text-center p-4"
    >
      Keine Energieknoten konfiguriert.<br />Bitte Widget konfigurieren.
    </div>

    <svg
      v-else
      :viewBox="`0 0 ${VB} ${VB}`"
      class="flex-1 w-full min-h-0"
      xmlns="http://www.w3.org/2000/svg"
    >
      <!-- Paths for animateMotion: from entity → center -->
      <defs>
        <path
          v-for="(pos, i) in positions"
          :key="`ef-path-${i}`"
          :id="`ef-path-${i}`"
          :d="`M ${pos.x} ${pos.y} L ${CX} ${CY}`"
        />
      </defs>

      <!-- Connecting lines (use node color) -->
      <line
        v-for="(pos, i) in positions"
        :key="`ef-line-${i}`"
        :x1="CX" :y1="CY"
        :x2="pos.x" :y2="pos.y"
        stroke-width="1.5"
        :stroke="displays[i].color"
        :stroke-opacity="displays[i].active ? 0.55 : 0.2"
      />

      <!-- Animated flow dots (node color, direction config-driven) -->
      <circle
        v-for="(d, i) in displays"
        v-show="d.active"
        :key="`ef-dot-${i}`"
        :data-testid="`ef-dot-${i}`"
        r="3.5"
        :fill="d.color"
      >
        <animateMotion
          :dur="animDur(d.power)"
          repeatCount="indefinite"
          :keyPoints="d.isSource ? '0;1' : '1;0'"
          keyTimes="0;1"
          calcMode="linear"
        >
          <mpath :href="`#ef-path-${i}`" />
        </animateMotion>
      </circle>

      <!-- Center: house -->
      <circle
        :cx="CX" :cy="CY" r="22"
        fill="transparent"
        stroke="#6b7280"
        stroke-width="1.5"
        stroke-opacity="0.6"
      />
      <!-- Icon: mittig wenn kein Hausverbrauch, sonst leicht nach oben -->
      <text
        :x="CX"
        :y="houseDisplay ? CY - 5 : CY"
        text-anchor="middle"
        dominant-baseline="central"
        :font-size="houseDisplay ? 20 : 26"
        style="user-select: none;"
      >🏠</text>
      <!-- Hausverbrauch unter dem Icon -->
      <text
        v-if="houseDisplay"
        :x="CX" :y="CY + 12"
        text-anchor="middle"
        dominant-baseline="central"
        font-size="7"
        fill="#d1d5db"
        data-testid="ef-house-value"
      >{{ houseDisplay }}</text>

      <!-- Entity nodes -->
      <g v-for="(d, i) in displays" :key="`ef-entity-${i}`">
        <!-- Entity circle (node color) -->
        <circle
          :cx="positions[i].x"
          :cy="positions[i].y"
          :r="ENTITY_R"
          fill="transparent"
          stroke-width="1.5"
          :stroke="d.color"
          :stroke-opacity="d.active ? 0.85 : 0.35"
        />
        <!-- Entity icon -->
        <text
          :x="positions[i].x"
          :y="positions[i].y"
          text-anchor="middle"
          dominant-baseline="central"
          font-size="17"
          style="user-select: none;"
        >{{ d.icon }}</text>

        <!-- Entity label -->
        <text
          :x="getTextLayout(positions[i]).labelX"
          :y="getTextLayout(positions[i]).labelY"
          :text-anchor="getTextLayout(positions[i]).anchor"
          dominant-baseline="auto"
          font-size="8"
          fill="#9ca3af"
        >{{ d.label }}</text>

        <!-- Power value (node color when active) -->
        <text
          :x="getTextLayout(positions[i]).valueX"
          :y="getTextLayout(positions[i]).valueY"
          :text-anchor="getTextLayout(positions[i]).anchor"
          dominant-baseline="auto"
          font-size="8"
          :fill="d.active ? d.color : '#6b7280'"
          :data-testid="`ef-value-${i}`"
        >{{ d.displayValue }}</text>
      </g>
    </svg>
  </div>
</template>
