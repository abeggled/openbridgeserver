<script setup lang="ts">
/**
 * VisuEditor — Vollständiger grafischer Drag & Drop Grid-Editor
 *
 * Features:
 * - Echte Widget-Vorschauen (editorMode=true)
 * - Drag to move (Snap to Grid)
 * - Resize Handle unten-rechts (Snap to Grid)
 * - Click to select, Delete-Taste zum Löschen
 * - DataPoint-Suche im Config-Panel
 * - Optionaler Status-Datenpunkt für schreibende Widgets
 * - Grid-Linien als visuelle Orientierung
 * - Keine externe Grid-Bibliothek (pure Vue + CSS)
 */
import {
  computed, onMounted, onUnmounted, ref, shallowRef, nextTick,
} from 'vue'

/** UUID-Generator mit Fallback für non-HTTPS Umgebungen */
function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useThemeStore } from '@/stores/theme'
import { WidgetRegistry } from '@/widgets/registry'
import MissingWidget from '@/widgets/MissingWidget.vue'
import DataPointPicker from '@/components/DataPointPicker.vue'
import Breadcrumb from '@/components/Breadcrumb.vue'
import AuthButton from '@/components/AuthButton.vue'
import { datapoints as dpApi } from '@/api/client'
import type { PageConfig, WidgetInstance } from '@/types'

import '@/widgets/ValueDisplay/index'
import '@/widgets/Toggle/index'
import '@/widgets/Slider/index'
import '@/widgets/Chart/index'
import '@/widgets/Link/index'
import '@/widgets/WidgetRef/index'
import '@/widgets/Info/index'
import '@/widgets/Text/index'
import '@/widgets/Zeitschaltuhr/index'
import '@/widgets/Rolladen/index'
import '@/widgets/Licht/index'
import '@/widgets/Fenster/index'
import '@/widgets/Energiefluss/index'
import '@/widgets/Kamera/index'
import '@/widgets/QrCode/index'
import '@/widgets/IFrame/index'
import '@/widgets/Uhr/index'
import '@/widgets/RTR/index'
import '@/widgets/Wetter/index'

// ── Props / Router / Store ────────────────────────────────────────────────────
const props = defineProps<{ id: string }>()
const router = useRouter()
const store = useVisuStore()
const theme = useThemeStore()

// ── State ─────────────────────────────────────────────────────────────────────
const isNew   = computed(() => props.id === 'new')
const newPageName = ref('Neue Seite')

const loading = ref(true)
const saving  = ref(false)
const error   = ref('')

const config = ref<PageConfig>({
  grid_cols: 12, grid_row_height: 80, grid_cell_width: 80, background: null, widgets: [],
})

const selectedId = ref<string | null>(null)
const selectedWidget = computed(() =>
  config.value.widgets.find(w => w.id === selectedId.value) ?? null,
)
const selectedDef = computed(() =>
  selectedWidget.value ? WidgetRegistry.get(selectedWidget.value.type) : null,
)

// ── Datenpunkt-Validierung ────────────────────────────────────────────────────
const brokenDpIds = ref<Set<string>>(new Set())
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function collectWidgetDpIds(w: WidgetInstance): string[] {
  const ids = new Set<string>()
  if (w.datapoint_id) ids.add(w.datapoint_id)
  if (w.status_datapoint_id) ids.add(w.status_datapoint_id)
  for (const val of Object.values(w.config ?? {})) {
    if (typeof val === 'string' && UUID_RE.test(val)) ids.add(val)
  }
  return [...ids]
}

async function validateDatapointRefs() {
  const allIds = new Set<string>()
  for (const w of config.value.widgets) {
    for (const id of collectWidgetDpIds(w)) allIds.add(id)
  }
  const broken = new Set<string>()
  await Promise.allSettled(
    [...allIds].map(async (id) => {
      try { await dpApi.get(id) } catch { broken.add(id) }
    })
  )
  brokenDpIds.value = broken
}

function widgetHasError(w: WidgetInstance): boolean {
  return collectWidgetDpIds(w).some(id => brokenDpIds.value.has(id))
}

// ── Canvas ────────────────────────────────────────────────────────────────────
const canvasEl = ref<HTMLElement | null>(null)

const COLS   = computed(() => config.value.grid_cols)
const CELL_H = computed(() => config.value.grid_row_height)
// Feste Zellbreite in Pixeln — identisch mit Viewer (WYSIWYG)
const CELL_W = computed(() => config.value.grid_cell_width ?? 80)

// Canvas-Breite = COLS × CELL_W (feste Pixelgrösse, kein relativer Stretch)
const canvasGridWidth = computed(() => COLS.value * CELL_W.value)

// Canvas-Höhe: höchstes Widget + 8 leere Zeilen
const canvasHeight = computed(() => {
  const maxRow = config.value.widgets.reduce((m, w) => Math.max(m, w.y + w.h), 0)
  return (maxRow + 8) * CELL_H.value
})

// Grid-Linien als CSS-Hintergrund
const gridBg = computed(() => {
  const cw = CELL_W.value
  const ch = CELL_H.value
  const lineColor = theme.isDark ? '#1f2937' : '#e5e7eb'
  return `
    repeating-linear-gradient(to right,  ${lineColor} 0, ${lineColor} 1px, transparent 1px, transparent ${cw}px),
    repeating-linear-gradient(to bottom, ${lineColor} 0, ${lineColor} 1px, transparent 1px, transparent ${ch}px)
  `
})

// ── Widget-Positionen ─────────────────────────────────────────────────────────
function widgetStyle(w: WidgetInstance) {
  return {
    left:   `${w.x * CELL_W.value}px`,
    top:    `${w.y * CELL_H.value}px`,
    width:  `${w.w * CELL_W.value}px`,
    height: `${w.h * CELL_H.value}px`,
  }
}

// ── Drag & Resize ─────────────────────────────────────────────────────────────
interface DragState {
  type: 'move' | 'resize'
  widgetId: string
  startMX: number; startMY: number
  startX: number;  startY: number   // Grid-Einheiten
  startW: number;  startH: number
}
const drag = shallowRef<DragState | null>(null)

function startDrag(e: MouseEvent, w: WidgetInstance) {
  if (e.button !== 0) return
  e.preventDefault()
  selectedId.value = w.id
  drag.value = {
    type: 'move', widgetId: w.id,
    startMX: e.clientX, startMY: e.clientY,
    startX: w.x, startY: w.y, startW: w.w, startH: w.h,
  }
}

function startResize(e: MouseEvent, w: WidgetInstance) {
  if (e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  selectedId.value = w.id
  drag.value = {
    type: 'resize', widgetId: w.id,
    startMX: e.clientX, startMY: e.clientY,
    startX: w.x, startY: w.y, startW: w.w, startH: w.h,
  }
}

function onMouseMove(e: MouseEvent) {
  if (!drag.value) return
  const w = config.value.widgets.find(x => x.id === drag.value!.widgetId)
  if (!w) return

  const dx = Math.round((e.clientX - drag.value.startMX) / CELL_W.value)
  const dy = Math.round((e.clientY - drag.value.startMY) / CELL_H.value)
  const def = WidgetRegistry.get(w.type)
  const minW = def?.minW ?? 1
  const minH = def?.minH ?? 1

  if (drag.value.type === 'move') {
    w.x = Math.max(0, Math.min(COLS.value - w.w, drag.value.startX + dx))
    w.y = Math.max(0, drag.value.startY + dy)
  } else {
    w.w = Math.max(minW, Math.min(COLS.value - w.x, drag.value.startW + dx))
    w.h = Math.max(minH, drag.value.startH + dy)
  }
}

function onMouseUp() {
  drag.value = null
}

// ── Tastatur ──────────────────────────────────────────────────────────────────
function onKeyDown(e: KeyboardEvent) {
  if (!selectedId.value) return
  if (e.key === 'Delete' || e.key === 'Backspace') {
    if ((e.target as HTMLElement).tagName === 'INPUT' ||
        (e.target as HTMLElement).tagName === 'TEXTAREA') return
    removeSelected()
  }
  if (e.key === 'Escape') selectedId.value = null
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))

// ── Laden ─────────────────────────────────────────────────────────────────────
onMounted(async () => {
  try {
    if (!isNew.value) {
      if (!store.treeLoaded) await store.loadTree()
      await store.loadBreadcrumb(props.id)
      await store.loadPage(props.id)
      if (store.pageConfig) config.value = JSON.parse(JSON.stringify(store.pageConfig))
      // Rückwärtskompatibilität: fehlende Felder nachrüsten
      if (!config.value.grid_cell_width) config.value.grid_cell_width = 80
      for (const w of config.value.widgets) {
        w.status_datapoint_id ??= null
        w.name ??= ''
      }
      // Datenpunkt-Referenzen im Hintergrund validieren (non-blocking)
      validateDatapointRefs()
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden'
  } finally {
    loading.value = false
  }
})

// ── Widget einfügen ───────────────────────────────────────────────────────────
function insertWidget(type: string) {
  const def = WidgetRegistry.get(type)
  if (!def) return

  const existingXY = new Set(
    config.value.widgets.flatMap(w =>
      Array.from({ length: w.w * w.h }, (_, i) =>
        `${w.x + (i % w.w)},${w.y + Math.floor(i / w.w)}`
      )
    )
  )
  let px = 0, py = 0
  outer: for (py = 0; py < 100; py++) {
    for (px = 0; px <= COLS.value - def.defaultW; px++) {
      const fits = Array.from({ length: def.defaultW * def.defaultH }, (_, i) =>
        !existingXY.has(`${px + (i % def.defaultW)},${py + Math.floor(i / def.defaultW)}`)
      ).every(Boolean)
      if (fits) break outer
    }
  }

  const w: WidgetInstance = {
    id: newId(),
    name: '',
    type,
    datapoint_id: null,
    status_datapoint_id: null,
    x: px, y: py,
    w: def.defaultW, h: def.defaultH,
    config: { ...def.defaultConfig },
  }
  config.value.widgets.push(w)
  selectedId.value = w.id

  nextTick(() => {
    canvasEl.value?.parentElement?.scrollTo({ top: py * CELL_H.value - 100, behavior: 'smooth' })
  })
}

// ── Widget entfernen ──────────────────────────────────────────────────────────
function removeSelected() {
  config.value.widgets = config.value.widgets.filter(w => w.id !== selectedId.value)
  selectedId.value = null
}

// ── Config aktualisieren ──────────────────────────────────────────────────────
function updateConfig(newCfg: Record<string, unknown>) {
  if (!selectedWidget.value) return
  selectedWidget.value.config = newCfg
}

function setDataPoint(id: string | null) {
  if (!selectedWidget.value) return
  selectedWidget.value.datapoint_id = id
}

function setStatusDataPoint(id: string | null) {
  if (!selectedWidget.value) return
  selectedWidget.value.status_datapoint_id = id
}

// ── Speichern ─────────────────────────────────────────────────────────────────
async function save() {
  saving.value = true
  error.value = ''
  try {
    if (isNew.value) {
      const node = await store.createNode({ name: newPageName.value.trim() || 'Neue Seite', type: 'PAGE', parent_id: null })
      await store.savePage(node.id, config.value)
      router.push({ name: 'viewer', params: { id: node.id } })
    } else {
      await store.savePage(props.id, config.value)
      router.push({ name: 'viewer', params: { id: props.id } })
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}

// ── Grid-Einstellungen ────────────────────────────────────────────────────────
const showSettings = ref(false)
</script>

<template>
  <div
    class="h-screen flex flex-col bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 overflow-hidden"
    @mousemove="onMouseMove"
    @mouseup="onMouseUp"
    @mouseleave="onMouseUp"
  >
    <!-- ── Toolbar ──────────────────────────────────────────────────────────── -->
    <header class="border-b border-gray-200 dark:border-gray-800 px-4 py-2 flex items-center gap-3 flex-shrink-0 bg-gray-50 dark:bg-gray-900">
      <Breadcrumb />
      <span class="text-xs font-medium text-blue-500 dark:text-blue-400 bg-blue-500/10 dark:bg-blue-400/10 px-2 py-0.5 rounded">Editor</span>
      <input
        v-if="isNew"
        v-model="newPageName"
        type="text"
        placeholder="Seitenname …"
        class="text-sm border border-gray-300 dark:border-gray-600 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-48"
      />
      <div class="flex-1" />

      <!-- Hell/Dunkel -->
      <button
        class="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 px-2 py-1 rounded transition-colors"
        :title="theme.isDark ? 'Heller Modus' : 'Dunkler Modus'"
        @click="theme.toggle()"
      >{{ theme.isDark ? '☀️' : '🌙' }}</button>
      <AuthButton />

      <!-- Grid-Einstellungen -->
      <button
        class="text-xs text-gray-400 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 px-2 py-1 rounded transition-colors"
        @click="showSettings = !showSettings"
        title="Grid-Einstellungen"
      >⚙️</button>

      <button
        class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors px-3 py-1.5 rounded border border-gray-300 dark:border-gray-700"
        @click="router.push(isNew ? { name: 'tree' } : { name: 'viewer', params: { id } })"
      >Abbrechen</button>

      <button
        class="text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg transition-colors font-medium"
        :disabled="saving"
        @click="save"
      >{{ saving ? 'Speichere …' : '💾 Speichern' }}</button>
    </header>

    <!-- Grid-Einstellungen Panel -->
    <div v-if="showSettings" class="border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 px-4 py-3 flex items-center gap-6 text-sm flex-wrap">
      <label class="flex items-center gap-2 text-gray-500 dark:text-gray-400">
        Spalten
        <input v-model.number="config.grid_cols" type="number" min="4" max="48"
          class="w-16 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 text-gray-900 dark:text-gray-100 text-xs focus:outline-none focus:border-blue-500" />
      </label>
      <label class="flex items-center gap-2 text-gray-500 dark:text-gray-400">
        Zellbreite (px)
        <input v-model.number="config.grid_cell_width" type="number" min="40" max="300" step="5"
          class="w-20 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 text-gray-900 dark:text-gray-100 text-xs focus:outline-none focus:border-blue-500" />
      </label>
      <label class="flex items-center gap-2 text-gray-500 dark:text-gray-400">
        Zeilenhöhe (px)
        <input v-model.number="config.grid_row_height" type="number" min="40" max="300" step="5"
          class="w-20 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 text-gray-900 dark:text-gray-100 text-xs focus:outline-none focus:border-blue-500" />
      </label>
      <span class="text-xs text-gray-400 dark:text-gray-600">
        Zelle: {{ CELL_W }}×{{ CELL_H }}px · Grid: {{ canvasGridWidth }}px breit
      </span>
    </div>

    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">Lade …</div>
    <div v-else-if="error" class="flex-1 flex items-center justify-center text-red-500 dark:text-red-400">{{ error }}</div>

    <div v-else class="flex-1 flex min-h-0">

      <!-- ── Widget-Palette (links) ───────────────────────────────────────── -->
      <aside class="w-48 flex-shrink-0 bg-gray-50 dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 overflow-y-auto">
        <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider px-3 pt-3 pb-2">Widgets</p>
        <div class="space-y-0.5 px-2 pb-3">
          <button
            v-for="w in WidgetRegistry.all()"
            :key="w.type"
            class="w-full flex items-center gap-2.5 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-white transition-colors text-left"
            @click="insertWidget(w.type)"
          >
            <span class="text-lg leading-none w-6 text-center">{{ w.icon }}</span>
            <span class="text-sm">{{ w.label }}</span>
          </button>
        </div>
        <div class="px-3 pt-2 pb-3 border-t border-gray-200 dark:border-gray-700">
          <p class="text-xs text-gray-400 dark:text-gray-600">Klick → einfügen</p>
          <p class="text-xs text-gray-400 dark:text-gray-600">Ziehen → verschieben</p>
          <p class="text-xs text-gray-400 dark:text-gray-600">↘ Handle → Größe ändern</p>
          <p class="text-xs text-gray-400 dark:text-gray-600">Entf → Widget löschen</p>
        </div>
      </aside>

      <!-- ── Grid-Canvas (Mitte) ─────────────────────────────────────────── -->
      <div class="flex-1 overflow-auto bg-white dark:bg-gray-950">
        <div
          ref="canvasEl"
          class="relative"
          :style="{
            width: canvasGridWidth + 'px',
            minWidth: '100%',
            height: canvasHeight + 'px',
            backgroundImage: gridBg,
            backgroundSize: `${CELL_W}px ${CELL_H}px`,
            cursor: drag ? (drag.type === 'move' ? 'grabbing' : 'se-resize') : 'default',
            userSelect: drag ? 'none' : 'auto',
          }"
          @click.self="selectedId = null"
        >
          <!-- Widgets -->
          <div
            v-for="w in config.widgets"
            :key="w.id"
            class="absolute overflow-hidden rounded-xl border-2 transition-[border-color,box-shadow] group"
            :class="[
              selectedId === w.id
                ? 'border-blue-500 shadow-lg shadow-blue-500/30 z-10'
                : 'border-gray-300 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 z-0',
              drag?.widgetId === w.id && drag?.type === 'move' ? 'opacity-90' : '',
            ]"
            :style="widgetStyle(w)"
            @mousedown="startDrag($event, w)"
            @click.stop="selectedId = w.id"
          >
            <!-- Widget-Vorschau (echte Komponente, editorMode=true) -->
            <div class="w-full h-full bg-gray-100 dark:bg-gray-800 pointer-events-none">
              <component
                :is="WidgetRegistry.get(w.type)?.component"
                v-if="WidgetRegistry.get(w.type)"
                :config="w.config"
                :datapoint-id="w.datapoint_id"
                :value="null"
                :status-value="null"
                :editor-mode="true"
                :h="w.h"
              />
              <MissingWidget v-else :widget-type="w.type" />
            </div>

            <!-- Fehler-Badge: fehlende Datenpunkt-Referenz -->
            <div
              v-if="widgetHasError(w)"
              class="absolute top-1 right-7 z-20 flex items-center justify-center w-5 h-5 rounded-full bg-red-500 text-white font-bold text-xs leading-none pointer-events-none shadow"
              title="Mindestens ein Datenpunkt-Verweis wurde nicht gefunden"
            >!</div>

            <!-- Widget-Label (nur sichtbar wenn selektiert oder hover) -->
            <div
              class="absolute top-0 left-0 right-0 flex items-center justify-between px-2 py-1 bg-white/80 dark:bg-gray-900/80 backdrop-blur-sm opacity-0 group-hover:opacity-100 transition-opacity"
              :class="{ '!opacity-100': selectedId === w.id }"
            >
              <span class="text-xs text-gray-700 dark:text-gray-300 font-medium">
                {{ w.name || (WidgetRegistry.get(w.type)?.label ?? w.type) }}
              </span>
              <button
                class="text-xs text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400 transition-colors ml-2"
                @click.stop="() => { selectedId = w.id; removeSelected() }"
              >✕</button>
            </div>

            <!-- Resize-Handle (unten-rechts) -->
            <div
              class="absolute bottom-0 right-0 w-5 h-5 flex items-end justify-end pb-1 pr-1 cursor-se-resize opacity-0 group-hover:opacity-100 transition-opacity"
              :class="{ '!opacity-100': selectedId === w.id }"
              @mousedown.stop="startResize($event, w)"
            >
              <svg width="10" height="10" viewBox="0 0 10 10" class="text-gray-400">
                <path d="M2 9 L9 2 M5 9 L9 5 M8 9 L9 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
            </div>
          </div>

          <!-- Leerer Zustand -->
          <div
            v-if="config.widgets.length === 0"
            class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-gray-400 dark:text-gray-600 pointer-events-none"
          >
            <span class="text-5xl">📐</span>
            <span class="text-sm">Widget aus der Palette links einfügen</span>
          </div>
        </div>
      </div>

      <!-- ── Config-Panel (rechts) ───────────────────────────────────────── -->
      <aside class="w-72 flex-shrink-0 bg-gray-50 dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 overflow-y-auto flex flex-col">
        <template v-if="selectedWidget && selectedDef">
          <!-- Header -->
          <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-100/50 dark:bg-gray-800/50">
            <div class="flex items-center gap-2">
              <span class="text-lg">{{ selectedDef.icon }}</span>
              <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ selectedDef.label }}</span>
            </div>
            <button
              class="text-xs text-red-500 dark:text-red-400 hover:text-red-600 dark:hover:text-red-300 transition-colors flex items-center gap-1 px-2 py-1 rounded hover:bg-red-500/10"
              @click="removeSelected"
            >
              🗑 Entfernen
            </button>
          </div>

          <div class="p-4 space-y-5 flex-1">
            <!-- Widget-Name -->
            <div>
              <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">Name</p>
              <input
                v-model="selectedWidget.name"
                type="text"
                placeholder="z.B. Wohnzimmer Dimmer"
                class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>

            <!-- Position & Größe -->
            <div>
              <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">Position & Größe</p>
              <div class="grid grid-cols-4 gap-1.5 text-xs">
                <div>
                  <label class="block text-gray-400 dark:text-gray-500 mb-0.5">X</label>
                  <input v-model.number="selectedWidget.x" type="number" min="0"
                    :max="COLS - selectedWidget.w"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="block text-gray-400 dark:text-gray-500 mb-0.5">Y</label>
                  <input v-model.number="selectedWidget.y" type="number" min="0"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="block text-gray-400 dark:text-gray-500 mb-0.5">B</label>
                  <input v-model.number="selectedWidget.w" type="number" :min="selectedDef.minW" :max="COLS"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="block text-gray-400 dark:text-gray-500 mb-0.5">H</label>
                  <input v-model.number="selectedWidget.h" type="number" :min="selectedDef.minH"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
                </div>
              </div>
            </div>

            <!-- Objekt (Schreib-/Lese-Objekt) -->
            <div v-if="!selectedDef.noDatapoint">
              <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">
                {{ selectedDef.supportsStatusDatapoint ? 'Schreib-Objekt' : 'Objekt' }}
              </p>
              <DataPointPicker
                :model-value="selectedWidget.datapoint_id"
                :compatible-types="selectedDef.compatibleTypes"
                @update:model-value="setDataPoint"
              />
              <p class="text-xs text-gray-400 dark:text-gray-600 mt-1">
                Kompatibel: {{ selectedDef.compatibleTypes.join(', ') }}
              </p>
            </div>

            <!-- Status-Objekt (nur für schreibende Widgets) -->
            <div v-if="selectedDef.supportsStatusDatapoint">
              <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                Status-Objekt
              </p>
              <p class="text-xs text-gray-400 dark:text-gray-600 mb-2">
                Optional: separates Rückmelde-Objekt. Falls leer, wird das Schreib-Objekt für die Anzeige verwendet.
              </p>
              <DataPointPicker
                :model-value="selectedWidget.status_datapoint_id"
                :compatible-types="selectedDef.compatibleTypes"
                @update:model-value="setStatusDataPoint"
              />
            </div>

            <!-- Widget-Config -->
            <div>
              <p class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">Konfiguration</p>
              <component
                :is="selectedDef.configComponent"
                :key="selectedWidget.id"
                :model-value="selectedWidget.config"
                @update:model-value="updateConfig"
              />
            </div>
          </div>
        </template>

        <!-- Kein Widget gewählt -->
        <div v-else class="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
          <span class="text-4xl text-gray-300 dark:text-gray-700">👆</span>
          <p class="text-sm text-gray-400 dark:text-gray-500">Widget auf dem Canvas anklicken<br />oder aus der Palette einfügen</p>
          <p class="text-xs text-gray-400 dark:text-gray-600 mt-2">
            {{ config.widgets.length }} Widget{{ config.widgets.length !== 1 ? 's' : '' }} auf dieser Seite
          </p>
        </div>
      </aside>

    </div>
  </div>
</template>
