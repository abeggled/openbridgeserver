<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { datapoints } from '@/api/client'
import { useDatapointsStore } from '@/stores/datapoints'
import type { DataPointValue } from '@/types'

/** Schwellwert in ms: darunter = Kurzklick (Schritt/Stop), darüber = Langdruck (Fahren) */
const LONG_PRESS_MS = 300

/** Schrittweite für Lamellen-Stufentasten in % */
const SLAT_STEP = 10

/** Tastenstatus für Richtungstasten */
type PressState = 'idle' | 'pressing' | 'moving'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const dpStore = useDatapointsStore()

const label      = computed(() => (props.config.label           as string)  ?? '—')
const mode       = computed(() => (props.config.mode            as string)  ?? 'rolladen')
const invert     = computed(() => (props.config.invert          as boolean) ?? false)
const invertUp   = computed(() => (props.config.invert_move_up  as boolean) ?? false)
const invertDown = computed(() => (props.config.invert_move_down as boolean) ?? false)

// ── DP-IDs aus der Config ────────────────────────────────────────────────────
const dpMoveUp         = computed(() => (props.config.dp_move_up          as string) || null)
const dpMoveDown       = computed(() => (props.config.dp_move_down        as string) || null)
const dpStop           = computed(() => (props.config.dp_stop             as string) || null)
const dpPosition       = computed(() => (props.config.dp_position         as string) || null)
const dpPositionStatus = computed(() => (props.config.dp_position_status  as string) || null)
const dpSlat           = computed(() => (props.config.dp_slat             as string) || null)
const dpSlatStatus     = computed(() => (props.config.dp_slat_status      as string) || null)

// ── Sperr- / Status-Datenpunkte ──────────────────────────────────────────────
const dpLock    = computed(() => (props.config.dp_lock    as string) || null)
const dpStatus1 = computed(() => (props.config.dp_status_1 as string) || null)
const dpStatus2 = computed(() => (props.config.dp_status_2 as string) || null)
const dpStatus3 = computed(() => (props.config.dp_status_3 as string) || null)
const dpStatus4 = computed(() => (props.config.dp_status_4 as string) || null)

const labelStatus1 = computed(() => (props.config.label_status_1 as string) || 'Manuelle Sperre')
const labelStatus2 = computed(() => (props.config.label_status_2 as string) || 'Status 2')
const labelStatus3 = computed(() => (props.config.label_status_3 as string) || 'Status 3')
const labelStatus4 = computed(() => (props.config.label_status_4 as string) || 'Status 4')

// ── Werte aus dem Store lesen ────────────────────────────────────────────────
function toNumber(id: string | null): number | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v) return null
  if (typeof v.v === 'number') return v.v
  const p = parseFloat(String(v.v))
  return isNaN(p) ? null : p
}

function getBool(id: string | null): boolean | null {
  if (!id) return null
  const v = dpStore.getValue(id)
  if (!v || v.v === null || v.v === undefined) return null
  if (typeof v.v === 'boolean') return v.v
  if (typeof v.v === 'number')  return v.v !== 0
  const s = String(v.v).toLowerCase()
  if (s === 'true'  || s === '1') return true
  if (s === 'false' || s === '0') return false
  return null
}

const lockValue    = computed(() => getBool(dpLock.value))
const status1Value = computed(() => getBool(dpStatus1.value))
const status2Value = computed(() => getBool(dpStatus2.value))
const status3Value = computed(() => getBool(dpStatus3.value))
const status4Value = computed(() => getBool(dpStatus4.value))

const hasLockOrStatus = computed(() =>
  !!(dpLock.value || dpStatus1.value || dpStatus2.value || dpStatus3.value || dpStatus4.value)
)

const rawPosition = computed(() => toNumber(dpPositionStatus.value ?? dpPosition.value))

/** Anzeigeposition: 0 = auf/hochgefahren, 100 = zu/runtergefahren */
const displayPosition = computed<number | null>(() => {
  if (rawPosition.value === null) return null
  return invert.value ? 100 - rawPosition.value : rawPosition.value
})

const rawSlat = computed(() => {
  if (mode.value !== 'jalousie') return null
  return toNumber(dpSlatStatus.value ?? dpSlat.value)
})

// ── Lokale Slider-Werte (optimistisch) ──────────────────────────────────────
const localPosition = ref<number | null>(null)
const localSlat     = ref<number | null>(null)
let posTimer:  ReturnType<typeof setTimeout> | null = null
let slatTimer: ReturnType<typeof setTimeout> | null = null

const shownPosition = computed(() => localPosition.value ?? displayPosition.value ?? 0)
const shownSlat     = computed(() => localSlat.value ?? rawSlat.value ?? 0)

const blindCoverage = computed(() => {
  if (props.editorMode) return 50
  return Math.max(0, Math.min(100, shownPosition.value))
})

// ── Tastenstatus ─────────────────────────────────────────────────────────────
const upState   = ref<PressState>('idle')
const downState = ref<PressState>('idle')
let upTimer:   ReturnType<typeof setTimeout> | null = null
let downTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Hilfsfunktionen für Invertierung.
 * Aktiv = Befehl ist eingeschaltet (Taste gedrückt).
 * Inaktiv = Befehl zurückgesetzt (Taste losgelassen / Kurzklick-Ende).
 */
function activeVal(inv: boolean): boolean { return !inv }

async function write(id: string | null, value: unknown) {
  if (!id || props.editorMode || props.readonly) return
  try { await datapoints.write(id, value) } catch { /* ignore */ }
}

// ── Stop senden ──────────────────────────────────────────────────────────────
/**
 * Sendet den Stop-Befehl (nur true — KEIN nachfolgendes false).
 * Viele Aktoren interpretieren false auf dem Stop-DP als neuen Fahrbefehl
 * (z.B. „fahre zur Standardposition"), was ungewollte Bewegungen auslöst.
 * KNX-Telegramme werden auch bei unverändertem Wert erneut ausgelöst,
 * d.h. ein erneuter Stop-Klick sendet stets einen neuen true-Impuls.
 */
async function sendStop() {
  await write(dpStop.value, true)
}

// ── Hoch-Taste ──────────────────────────────────────────────────────────────
/**
 * Kurzklick (< 0.5 s): Schritt hoch / Lamellen öffnen
 *   → aktiv bei pointerdown, inaktiv bei pointerup  (kurzes Signal = Short-Travel/Schritt)
 *
 * Langdruck (≥ 0.5 s): Auffahren bis Endlage
 *   → aktiv bei pointerdown, beim Loslassen NICHTS senden → Aktor fährt eigenständig
 *     bis zur Endlage.  Abbruch nur über Stop-Taste.
 *
 * WICHTIG: Wir senden beim Loslassen nach Langdruck KEINEN inaktiven Wert!
 * Viele Aktoren interpretieren 0 auf dem Down-DP als „fahr hoch" (DPT 1.008).
 */
async function onMoveUpStart() {
  if (props.editorMode || props.readonly) return
  upState.value = 'pressing'
  // Richtungsbefehl erst nach LONG_PRESS_MS senden — erst dann ist Langdruck bestätigt.
  // Beim Kurzklick wird kein Richtungsbefehl gesendet → kein ungewollter Impuls beim Stop.
  upTimer = setTimeout(async () => {
    upState.value = 'moving'
    upTimer = null
    await write(dpMoveUp.value, activeVal(invertUp.value))
  }, LONG_PRESS_MS)
}

async function onMoveUpEnd() {
  if (upState.value === 'idle') return
  const wasShort = upState.value === 'pressing'   // Timer hat noch nicht ausgelöst
  if (upTimer) { clearTimeout(upTimer); upTimer = null }
  upState.value = 'idle'
  if (wasShort) {
    // Kurzklick-Ende: Stop-DP senden statt inaktiven Richtungswert.
    // Den inaktiven Wert (false) auf einem Richtungs-DP zu senden würde bei
    // vielen Aktoren (DPT 1.008: 0=hoch, 1=runter) die Gegenrichtung auslösen.
    await sendStop()
  }
  // Langdruck: nichts senden → Aktor fährt selbständig bis Endlage
}

// ── Runter-Taste ─────────────────────────────────────────────────────────────
async function onMoveDownStart() {
  if (props.editorMode || props.readonly) return
  downState.value = 'pressing'
  downTimer = setTimeout(async () => {
    downState.value = 'moving'
    downTimer = null
    await write(dpMoveDown.value, activeVal(invertDown.value))
  }, LONG_PRESS_MS)
}

async function onMoveDownEnd() {
  if (downState.value === 'idle') return
  const wasShort = downState.value === 'pressing'
  if (downTimer) { clearTimeout(downTimer); downTimer = null }
  downState.value = 'idle'
  if (wasShort) {
    await sendStop()
  }
  // Langdruck: nichts senden → Aktor fährt selbständig bis Endlage
}

// ── Stop-Taste ───────────────────────────────────────────────────────────────
async function onStop() {
  if (upTimer)   { clearTimeout(upTimer);   upTimer   = null }
  if (downTimer) { clearTimeout(downTimer); downTimer = null }
  upState.value   = 'idle'
  downState.value = 'idle'
  await sendStop()
}

// ── Positionsregler ──────────────────────────────────────────────────────────
function onPositionInput(e: Event) {
  localPosition.value = Number((e.target as HTMLInputElement).value)
}

async function onPositionChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localPosition.value = val
  if (posTimer) clearTimeout(posTimer)
  posTimer = setTimeout(() => { localPosition.value = null }, 5000)
  const sendVal = invert.value ? 100 - val : val
  await write(dpPosition.value, sendVal)
}

// ── Lamellenregler (Slider) ──────────────────────────────────────────────────
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

// ── Lamellen-Schrittfunktionen ───────────────────────────────────────────────
async function slatStep(dir: 'open' | 'close') {
  if (props.editorMode || props.readonly) return
  const current = localSlat.value ?? rawSlat.value ?? 0
  const next = Math.max(0, Math.min(100, dir === 'open' ? current - SLAT_STEP : current + SLAT_STEP))
  localSlat.value = next
  if (slatTimer) clearTimeout(slatTimer)
  slatTimer = setTimeout(() => { localSlat.value = null }, 5000)
  await write(dpSlat.value, next)
}

// ── Sperre umschalten ────────────────────────────────────────────────────────
async function toggleLock() {
  if (props.editorMode || props.readonly) return
  await write(dpLock.value, !(lockValue.value ?? false))
}

// ── SVG Lamellenansicht – Queransicht (entlang Rotationsachse) ───────────────
/**
 * Blickrichtung: entlang der Rotationsachse (gestrichelte Mittellinie).
 * Jede Lamelle erscheint als rotierende Linie:
 *   0 %  → waagerecht (Lamelle offen, flach)
 *   50 % → 45° diagonal (links-unten nach rechts-oben)
 *   100 %→ senkrecht (Lamelle geschlossen)
 */
const SLAT_COUNT_V    = 5
const SVG_VW          = 32
const SVG_VH          = 80
const SLAT_SPACING_V  = SVG_VH / (SLAT_COUNT_V + 1)   // ≈ 13.3 px
const SLAT_LINE_L     = SVG_VW - 8                      // 24 px Linienlänge

const slatLines = computed(() => {
  const rad = (shownSlat.value / 100) * 90 * (Math.PI / 180)
  const cx  = SVG_VW / 2
  const half = SLAT_LINE_L / 2
  return Array.from({ length: SLAT_COUNT_V }, (_, i) => {
    const cy = (i + 1) * SLAT_SPACING_V
    return {
      x1: cx - half * Math.cos(rad),
      y1: cy + half * Math.sin(rad),  // links-unten bei 45°
      x2: cx + half * Math.cos(rad),
      y2: cy - half * Math.sin(rad),  // rechts-oben bei 45°
    }
  })
})

// ── Tooltip-Texte ────────────────────────────────────────────────────────────
const tooltipUp   = 'Kurz: Stopp\nLang: Auffahren bis Endlage'
const tooltipDown = 'Kurz: Stopp\nLang: Abfahren bis Endlage'

const tooltipStop = 'Sofort stoppen'

// ── Sicherheits-Cleanup ──────────────────────────────────────────────────────
function onWindowPointerUp() {
  if (upState.value   !== 'idle') onMoveUpEnd()
  if (downState.value !== 'idle') onMoveDownEnd()
}

onMounted(() => window.addEventListener('pointerup', onWindowPointerUp))
onUnmounted(() => {
  window.removeEventListener('pointerup', onWindowPointerUp)
  if (upTimer)   clearTimeout(upTimer)
  if (downTimer) clearTimeout(downTimer)
  if (posTimer)  clearTimeout(posTimer)
  if (slatTimer) clearTimeout(slatTimer)
})
</script>

<template>
  <div class="flex flex-col h-full p-2 select-none gap-1.5">
    <!-- Label -->
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate leading-none">{{ label }}</span>

    <div class="flex flex-1 gap-2 min-h-0">
      <!-- Linke Spalte: Steuer-Buttons + Rollo-Visualisierung -->
      <div class="flex flex-col items-center gap-1">

        <!-- Hoch-Taste -->
        <button
          class="relative w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0 overflow-hidden"
          :class="{
            'bg-blue-500 text-white':                                                     upState === 'moving',
            'bg-amber-400 text-white':                                                    upState === 'pressing',
            'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 \
             hover:bg-blue-100 dark:hover:bg-blue-900 disabled:opacity-40':               upState === 'idle',
          }"
          :disabled="editorMode || readonly"
          :title="tooltipUp"
          @pointerdown.prevent="onMoveUpStart"
          @pointerup="onMoveUpEnd"
        >
          ▲
          <!-- Long-Press-Fortschrittsbalken (sichtbar solange pressing) -->
          <span
            v-if="upState === 'pressing'"
            class="absolute bottom-0 left-0 h-0.5 bg-blue-400 long-press-bar"
          />
        </button>

        <!-- Rollo-Visualisierung -->
        <div class="flex-1 w-7 relative rounded overflow-hidden border border-gray-300 dark:border-gray-600 bg-sky-100 dark:bg-sky-950 min-h-0">
          <div
            class="absolute top-0 left-0 right-0 transition-all duration-300"
            :class="mode === 'jalousie' ? 'bg-amber-300 dark:bg-amber-700' : 'bg-gray-400 dark:bg-gray-500'"
            :style="{ height: blindCoverage + '%' }"
          >
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
          class="w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0
                 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300
                 hover:bg-red-200 dark:hover:bg-red-900 disabled:opacity-40"
          :disabled="editorMode || readonly"
          :title="tooltipStop"
          @click="onStop"
        >■</button>

        <!-- Runter-Taste -->
        <button
          class="relative w-7 h-7 rounded flex items-center justify-center text-xs font-bold transition-colors shrink-0 overflow-hidden"
          :class="{
            'bg-blue-500 text-white':                                                       downState === 'moving',
            'bg-amber-400 text-white':                                                      downState === 'pressing',
            'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 \
             hover:bg-blue-100 dark:hover:bg-blue-900 disabled:opacity-40':                 downState === 'idle',
          }"
          :disabled="editorMode || readonly"
          :title="tooltipDown"
          @pointerdown.prevent="onMoveDownStart"
          @pointerup="onMoveDownEnd"
        >
          ▼
          <span
            v-if="downState === 'pressing'"
            class="absolute bottom-0 left-0 h-0.5 bg-blue-400 long-press-bar"
          />
        </button>

      </div>

      <!-- Mittlere Spalte: Schieberegler + Statuszeile -->
      <div class="flex flex-col flex-1">

        <!-- Schieberegler (vertikal zentriert) -->
        <div class="flex flex-col flex-1 justify-center gap-3">

          <!-- Positionsregler -->
          <div>
            <div class="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
              <span>Position</span>
              <span class="tabular-nums font-medium text-gray-700 dark:text-gray-300">
                {{ displayPosition !== null ? Math.round(shownPosition) + ' %' : '—' }}
              </span>
            </div>
            <input
              type="range" min="0" max="100" step="1"
              :value="shownPosition"
              :disabled="editorMode || readonly"
              class="w-full accent-blue-500 cursor-pointer disabled:cursor-default disabled:opacity-40"
              @input="onPositionInput"
              @change="onPositionChange"
            />
            <div class="flex justify-between text-xs text-gray-400 dark:text-gray-600 mt-0.5">
              <span>auf</span><span>zu</span>
            </div>
          </div>

          <!-- Lamellenregler (nur Jalousie) -->
          <div v-if="mode === 'jalousie'">
            <div class="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
              <span>Lamellen</span>
              <span class="tabular-nums font-medium text-gray-700 dark:text-gray-300">
                {{ rawSlat !== null ? Math.round(shownSlat) + ' %' : '—' }}
              </span>
            </div>
            <input
              type="range" min="0" max="100" step="1"
              :value="shownSlat"
              :disabled="editorMode || readonly"
              class="w-full accent-amber-500 cursor-pointer disabled:cursor-default disabled:opacity-40"
              @input="onSlatInput"
              @change="onSlatChange"
            />
            <div class="flex justify-between text-xs text-gray-400 dark:text-gray-600 mt-0.5">
              <span>offen</span><span>zu</span>
            </div>
          </div>

        </div>

        <!-- Statuszeile: Sperre (Ausgang) + 4 read-only Indikatoren -->
        <div v-if="hasLockOrStatus" class="flex gap-1 items-center shrink-0">

          <!-- Sperre (schaltbarer Ausgang) — nur Dot, kein Icon -->
          <button
            v-if="dpLock"
            class="w-7 h-7 rounded flex items-center justify-center transition-colors shrink-0
                   bg-gray-200 dark:bg-gray-700
                   hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-40"
            :disabled="editorMode || readonly"
            title="Sperre"
            @click="toggleLock"
          >
            <span
              class="w-3 h-3 rounded-full"
              :class="lockValue === null ? 'bg-gray-400 dark:bg-gray-500'
                                        : lockValue ? 'bg-red-500' : 'bg-green-500'"
            />
          </button>

          <!-- Status 1 (konfigurierbarer Name) -->
          <div
            v-if="dpStatus1"
            class="w-7 h-7 rounded flex items-center justify-center shrink-0 cursor-default
                   bg-gray-200 dark:bg-gray-700"
            :title="labelStatus1"
          >
            <span
              class="w-3 h-3 rounded-full"
              :class="status1Value === null ? 'bg-gray-400 dark:bg-gray-500'
                                           : status1Value ? 'bg-red-500' : 'bg-green-500'"
            />
          </div>

          <!-- Status 2 -->
          <div
            v-if="dpStatus2"
            class="w-7 h-7 rounded flex items-center justify-center shrink-0 cursor-default
                   bg-gray-200 dark:bg-gray-700"
            :title="labelStatus2"
          >
            <span
              class="w-3 h-3 rounded-full"
              :class="status2Value === null ? 'bg-gray-400 dark:bg-gray-500'
                                           : status2Value ? 'bg-red-500' : 'bg-green-500'"
            />
          </div>

          <!-- Status 3 -->
          <div
            v-if="dpStatus3"
            class="w-7 h-7 rounded flex items-center justify-center shrink-0 cursor-default
                   bg-gray-200 dark:bg-gray-700"
            :title="labelStatus3"
          >
            <span
              class="w-3 h-3 rounded-full"
              :class="status3Value === null ? 'bg-gray-400 dark:bg-gray-500'
                                           : status3Value ? 'bg-red-500' : 'bg-green-500'"
            />
          </div>

          <!-- Status 4 -->
          <div
            v-if="dpStatus4"
            class="w-7 h-7 rounded flex items-center justify-center shrink-0 cursor-default
                   bg-gray-200 dark:bg-gray-700"
            :title="labelStatus4"
          >
            <span
              class="w-3 h-3 rounded-full"
              :class="status4Value === null ? 'bg-gray-400 dark:bg-gray-500'
                                           : status4Value ? 'bg-red-500' : 'bg-green-500'"
            />
          </div>

        </div>
      </div>

      <!-- Rechte Spalte: Lamellenansicht Queransicht (nur Jalousie) -->
      <div
        v-if="mode === 'jalousie'"
        class="flex flex-col items-center gap-1 w-7 shrink-0"
      >
        <!-- Sonne = Lamellen öffnen (hell) -->
        <button
          class="w-7 h-7 rounded flex items-center justify-center text-sm transition-colors shrink-0
                 bg-gray-200 dark:bg-gray-700 text-yellow-500
                 hover:bg-amber-100 dark:hover:bg-amber-900 disabled:opacity-40"
          :disabled="editorMode || readonly"
          title="Lamellen öffnen (hell)"
          @click="slatStep('open')"
        >☀</button>

        <!-- SVG Queransicht: waagrechte Lamellen kreuzen Rotationsachse -->
        <svg
          :viewBox="`0 0 ${SVG_VW} ${SVG_VH}`"
          class="w-full flex-1 min-h-0 rounded border border-gray-300 dark:border-gray-600 bg-sky-50 dark:bg-sky-950"
          xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="xMidYMid meet"
        >
          <!-- Rotationsachse (gestrichelte Mittellinie) -->
          <line
            :x1="SVG_VW / 2" y1="0"
            :x2="SVG_VW / 2" :y2="SVG_VH"
            stroke-width="1"
            stroke-dasharray="3,2"
            class="stroke-gray-400 dark:stroke-gray-500"
          />
          <!-- Lamellen als rotierende Linien -->
          <line
            v-for="(s, i) in slatLines"
            :key="i"
            :x1="s.x1" :y1="s.y1"
            :x2="s.x2" :y2="s.y2"
            stroke-width="2.5"
            stroke-linecap="round"
            class="stroke-amber-500 dark:stroke-amber-400"
          />
        </svg>

        <!-- Mond = Lamellen schliessen (dunkel) -->
        <button
          class="w-7 h-7 rounded flex items-center justify-center text-sm transition-colors shrink-0
                 bg-gray-200 dark:bg-gray-700 text-blue-400
                 hover:bg-blue-100 dark:hover:bg-blue-900 disabled:opacity-40"
          :disabled="editorMode || readonly"
          title="Lamellen schliessen (dunkel)"
          @click="slatStep('close')"
        >🌙</button>
      </div>

    </div>
  </div>
</template>

<style scoped>
/**
 * Fortschrittsbalken am unteren Rand der Richtungstaste.
 * Füllt sich in genau LONG_PRESS_MS (300 ms) → visuelles Feedback für Langdruck.
 */
.long-press-bar {
  animation: longPressProgress 300ms linear forwards;
}

@keyframes longPressProgress {
  from { width: 0% }
  to   { width: 100% }
}
</style>
