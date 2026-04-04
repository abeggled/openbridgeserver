<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { datapoints } from '@/api/client'
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

const label  = computed(() => (props.config.label  as string)  ?? '—')
const mode   = computed(() => (props.config.mode   as string)  ?? 'rolladen')
const invert = computed(() => (props.config.invert as boolean) ?? false)

// DP-IDs aus der Config
const dpMoveUp         = computed(() => (props.config.dp_move_up          as string) || null)
const dpMoveDown       = computed(() => (props.config.dp_move_down        as string) || null)
const dpStop           = computed(() => (props.config.dp_stop             as string) || null)
const dpPosition       = computed(() => (props.config.dp_position         as string) || null)
const dpPositionStatus = computed(() => (props.config.dp_position_status  as string) || null)
const dpSlat           = computed(() => (props.config.dp_slat             as string) || null)
const dpSlatStatus     = computed(() => (props.config.dp_slat_status      as string) || null)

// Hilfsfunktion: DataPointValue → Zahl (0–100)
function toNumber(id: string | null): number | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v) return null
  if (typeof v.v === 'number') return v.v
  const p = parseFloat(String(v.v))
  return isNaN(p) ? null : p
}

// Positions-Rohwert vom Status-DP (Vorrang) oder Schreib-DP
const rawPosition = computed(() => toNumber(dpPositionStatus.value ?? dpPosition.value))

/**
 * Anzeigeposition (0 = offen/hochgefahren, 100 = geschlossen/runtergefahren).
 * Bei invert=true ist der KNX-Wert invertiert (0 = zu, 100 = auf).
 */
const displayPosition = computed<number | null>(() => {
  if (rawPosition.value === null) return null
  return invert.value ? 100 - rawPosition.value : rawPosition.value
})

// Lamellenwinkel (nur Jalousie)
const rawSlat = computed(() => {
  if (mode.value !== 'jalousie') return null
  return toNumber(dpSlatStatus.value ?? dpSlat.value)
})

// Lokale Slider-Werte (optimistisch bis KNX antwortet)
const localPosition = ref<number | null>(null)
const localSlat     = ref<number | null>(null)
let posTimer: ReturnType<typeof setTimeout> | null = null
let slatTimer: ReturnType<typeof setTimeout> | null = null

const shownPosition = computed(() => localPosition.value ?? displayPosition.value ?? 0)
const shownSlat     = computed(() => localSlat.value ?? rawSlat.value ?? 0)

// Visuelle Befüllung (0 % = Rollo hochgefahren, 100 % = Rollo unten)
const blindCoverage = computed(() => {
  if (props.editorMode) return 50
  return Math.max(0, Math.min(100, shownPosition.value))
})

const movingUp   = ref(false)
const movingDown = ref(false)

async function write(id: string | null, value: unknown) {
  if (!id || props.editorMode || props.readonly) return
  try { await datapoints.write(id, value) } catch { /* ignore */ }
}

async function onMoveUpStart() {
  if (props.editorMode || props.readonly) return
  movingUp.value = true
  await write(dpMoveUp.value, true)
}

async function onMoveUpEnd() {
  if (!movingUp.value) return
  movingUp.value = false
  await write(dpMoveUp.value, false)
}

async function onMoveDownStart() {
  if (props.editorMode || props.readonly) return
  movingDown.value = true
  await write(dpMoveDown.value, true)
}

async function onMoveDownEnd() {
  if (!movingDown.value) return
  movingDown.value = false
  await write(dpMoveDown.value, false)
}

async function onStop() {
  movingUp.value   = false
  movingDown.value = false
  await write(dpStop.value, true)
  setTimeout(() => write(dpStop.value, false), 200)
}

function onPositionInput(e: Event) {
  localPosition.value = Number((e.target as HTMLInputElement).value)
}

async function onPositionChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localPosition.value = val
  if (posTimer) clearTimeout(posTimer)
  posTimer = setTimeout(() => { localPosition.value = null }, 5000)
  // Invertierung rückgängig machen bevor an KNX senden
  const sendVal = invert.value ? 100 - val : val
  await write(dpPosition.value, sendVal)
}

function onSlatInput(e: Event) {
  localSlat.value = Number((e.target as HTMLInputElement).value)
}

async function onSlatChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localSlat.value = val
  if (slatTimer) clearTimeout(slatTimer)
  slatTimer = setTimeout(() => { localSlat.value = null }, 5000)
  await write(dpSlat.value, val)
}

// Sicherheits-Reset: pointerup ausserhalb der Buttons
function onWindowPointerUp() {
  if (movingUp.value)   onMoveUpEnd()
  if (movingDown.value) onMoveDownEnd()
}

onMounted(() => window.addEventListener('pointerup', onWindowPointerUp))
onUnmounted(() => {
  window.removeEventListener('pointerup', onWindowPointerUp)
  if (posTimer)  clearTimeout(posTimer)
  if (slatTimer) clearTimeout(slatTimer)
})
</script>

<template>
  <div class="flex flex-col h-full p-2 select-none gap-1.5">
    <!-- Label -->
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate leading-none">{{ label }}</span>

    <div class="flex flex-1 gap-2 min-h-0">
      <!-- Linke Spalte: Buttons + visuelle Rollo-Darstellung -->
      <div class="flex flex-col items-center gap-1">
        <!-- Hoch-Taste -->
        <button
          class="w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0"
          :class="movingUp
            ? 'bg-blue-500 text-white'
            : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-blue-100 dark:hover:bg-blue-900 disabled:opacity-40'"
          :disabled="editorMode || readonly"
          title="Hoch fahren"
          @pointerdown.prevent="onMoveUpStart"
          @pointerup="onMoveUpEnd"
          @pointerleave="onMoveUpEnd"
        >▲</button>

        <!-- Rollo-Visualisierung -->
        <div class="flex-1 w-7 relative rounded overflow-hidden border border-gray-300 dark:border-gray-600 bg-sky-100 dark:bg-sky-950 min-h-0">
          <!-- Befüllter Bereich = zugezogenes Rollo (von oben) -->
          <div
            class="absolute top-0 left-0 right-0 transition-all duration-300"
            :class="mode === 'jalousie' ? 'bg-amber-300 dark:bg-amber-700' : 'bg-gray-400 dark:bg-gray-500'"
            :style="{ height: blindCoverage + '%' }"
          >
            <!-- Lamellenlinien bei Jalousie -->
            <div
              v-if="mode === 'jalousie'"
              class="w-full h-full"
              :style="{
                backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.2) 3px, rgba(0,0,0,0.2) 4px)',
              }"
            />
          </div>
        </div>

        <!-- Stop-Taste -->
        <button
          class="w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-red-100 dark:hover:bg-red-900 disabled:opacity-40"
          :disabled="editorMode || readonly"
          title="Stop"
          @click="onStop"
        >■</button>

        <!-- Runter-Taste -->
        <button
          class="w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0"
          :class="movingDown
            ? 'bg-blue-500 text-white'
            : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-blue-100 dark:hover:bg-blue-900 disabled:opacity-40'"
          :disabled="editorMode || readonly"
          title="Runter fahren"
          @pointerdown.prevent="onMoveDownStart"
          @pointerup="onMoveDownEnd"
          @pointerleave="onMoveDownEnd"
        >▼</button>
      </div>

      <!-- Rechte Spalte: Schieberegler -->
      <div class="flex flex-col flex-1 justify-center gap-3">
        <!-- Positions-Regler -->
        <div>
          <div class="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
            <span>Position</span>
            <span class="tabular-nums font-medium text-gray-700 dark:text-gray-300">
              {{ displayPosition !== null ? Math.round(shownPosition) + ' %' : '—' }}
            </span>
          </div>
          <input
            type="range"
            min="0" max="100" step="1"
            :value="shownPosition"
            :disabled="editorMode || readonly || !dpPosition"
            class="w-full accent-blue-500 cursor-pointer disabled:cursor-default disabled:opacity-40"
            @input="onPositionInput"
            @change="onPositionChange"
          />
          <div class="flex justify-between text-xs text-gray-400 dark:text-gray-600 mt-0.5">
            <span>auf</span><span>zu</span>
          </div>
        </div>

        <!-- Lamellen-Regler (nur Jalousie) -->
        <div v-if="mode === 'jalousie'">
          <div class="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
            <span>Lamellen</span>
            <span class="tabular-nums font-medium text-gray-700 dark:text-gray-300">
              {{ rawSlat !== null ? Math.round(shownSlat) + ' %' : '—' }}
            </span>
          </div>
          <input
            type="range"
            min="0" max="100" step="1"
            :value="shownSlat"
            :disabled="editorMode || readonly || !dpSlat"
            class="w-full accent-amber-500 cursor-pointer disabled:cursor-default disabled:opacity-40"
            @input="onSlatInput"
            @change="onSlatChange"
          />
          <div class="flex justify-between text-xs text-gray-400 dark:text-gray-600 mt-0.5">
            <span>offen</span><span>zu</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
