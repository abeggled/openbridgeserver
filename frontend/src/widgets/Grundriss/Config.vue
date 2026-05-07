<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useVisuStore } from '@/stores/visu'
import { WidgetRegistry } from '@/widgets/registry'
import DataPointPicker from '@/components/DataPointPicker.vue'
import type { VisuNode } from '@/types'

// ── Types ─────────────────────────────────────────────────────────────────────

interface GrundrissArea {
  id: string
  name: string
  points: Array<[number, number]>    // image pixel coords (unrotated)
  showLabel: boolean
  labelX: number
  labelY: number
  labelColor: string
  actionType: 'none' | 'navigate'
  actionValue: string
}

interface GrundrissMiniWidget {
  id: string
  label: string
  widgetType: string
  config: Record<string, unknown>
  datapointId: string | null
  statusDatapointId: string | null
  x: number        // center in image natural pixels
  y: number        // center in image natural pixels
  wPx: number      // screen width in px
  hPx: number      // screen height in px
  visible: boolean
}

// ── Props / Emit ──────────────────────────────────────────────────────────────

const props = defineProps<{ modelValue: Record<string, unknown>; widgetId?: string }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

// ── Visu Store (page picker) ──────────────────────────────────────────────────

const store = useVisuStore()
onMounted(async () => { if (!store.treeLoaded) await store.loadTree() })

// ── Config ────────────────────────────────────────────────────────────────────

const cfg = reactive({
  image:         (props.modelValue.image         as string | null)        ?? null,
  imageNaturalW: (props.modelValue.imageNaturalW as number)               ?? 1920,
  imageNaturalH: (props.modelValue.imageNaturalH as number)               ?? 1080,
  rotation:      (props.modelValue.rotation      as 0|90|180|270)         ?? 0,
  showAreaNames: (props.modelValue.showAreaNames as boolean)              ?? true,
  areas:         (props.modelValue.areas         as GrundrissArea[])      ?? [],
  miniWidgets:   (props.modelValue.miniWidgets   as GrundrissMiniWidget[]) ?? [],
})

watch(cfg, () => emit('update:modelValue', {
  image:         cfg.image,
  imageNaturalW: cfg.imageNaturalW,
  imageNaturalH: cfg.imageNaturalH,
  rotation:      cfg.rotation,
  showAreaNames: cfg.showAreaNames,
  areas:         cfg.areas,
  miniWidgets:   cfg.miniWidgets,
}), { deep: true })

// ── Image Upload ──────────────────────────────────────────────────────────────

const fileInput     = ref<HTMLInputElement>()
const imageSizeWarn = ref(false)

function triggerFileInput() { fileInput.value?.click() }

function onFileChange(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  imageSizeWarn.value = file.size > 2 * 1024 * 1024
  const reader = new FileReader()
  reader.onload = (ev) => {
    const dataUrl = ev.target?.result as string
    cfg.image = dataUrl
    const img = new Image()
    img.onload = () => {
      cfg.imageNaturalW = img.naturalWidth  || 1920
      cfg.imageNaturalH = img.naturalHeight || 1080
    }
    img.src = dataUrl
  }
  reader.readAsDataURL(file)
  ;(e.target as HTMLInputElement).value = ''
}

// ── SVG helpers ───────────────────────────────────────────────────────────────

const strokeW    = computed(() => cfg.imageNaturalW * 0.0025)
const fontSize   = computed(() => cfg.imageNaturalW * 0.018)
const svgViewBox = computed(() => `0 0 ${cfg.imageNaturalW} ${cfg.imageNaturalH}`)

function ptStr(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x},${y}`).join(' ')
}

// ── ID generator ─────────────────────────────────────────────────────────────

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2, 10)
}

// ── Drawing State ─────────────────────────────────────────────────────────────

const drawingMode   = ref(false)
const currentPoints = ref<Array<[number, number]>>([])
const mousePos      = ref<[number, number]>([0, 0])

const closeThreshold = computed(() => cfg.imageNaturalW * 0.04)

function getImageCoords(e: MouseEvent, el: HTMLElement): [number, number] {
  const rect  = el.getBoundingClientRect()
  const scale = Math.min(rect.width / cfg.imageNaturalW, rect.height / cfg.imageNaturalH)
  const offX  = (rect.width  - cfg.imageNaturalW * scale) / 2
  const offY  = (rect.height - cfg.imageNaturalH * scale) / 2
  return [
    Math.max(0, Math.min(cfg.imageNaturalW, (e.clientX - rect.left - offX) / scale)),
    Math.max(0, Math.min(cfg.imageNaturalH, (e.clientY - rect.top  - offY) / scale)),
  ]
}

function isNearFirst(pos: [number, number]): boolean {
  if (currentPoints.value.length < 3) return false
  const [fx, fy] = currentPoints.value[0]
  const dx = pos[0] - fx
  const dy = pos[1] - fy
  return Math.sqrt(dx * dx + dy * dy) < closeThreshold.value
}

// After closing a polygon: keep drawing mode active so the user can draw the next area.
function finishArea() {
  if (currentPoints.value.length < 3) return
  const pts = [...currentPoints.value]
  const cx  = pts.reduce((s, [x]) => s + x, 0) / pts.length
  const cy  = pts.reduce((s, [, y]) => s + y, 0) / pts.length
  const area: GrundrissArea = {
    id:          newId(),
    name:        `Bereich ${cfg.areas.length + 1}`,
    points:      pts,
    showLabel:   true,
    labelX:      cx,
    labelY:      cy,
    labelColor:  '#ffffff',
    actionType:  'none',
    actionValue: '',
  }
  cfg.areas.push(area)
  currentPoints.value  = []
  selectedAreaId.value = area.id
}

function cancelCurrentPolygon() {
  currentPoints.value = []
}

// ── Fullscreen Modal ──────────────────────────────────────────────────────────

const fullscreenOpen = ref(false)
const canvasRef      = ref<HTMLDivElement>()
const widgetRect     = ref<DOMRect | null>(null)

function captureWidgetRect() {
  if (!props.widgetId) { widgetRect.value = null; return }
  const el = document.querySelector(`[data-widget-id="${props.widgetId}"]`)
  widgetRect.value = el ? (el as HTMLElement).getBoundingClientRect() : null
}

const canvasPositionStyle = computed(() => {
  const shadow = '0 0 0 9999px rgba(0,0,0,0.65)'
  if (widgetRect.value) {
    return {
      position:  'absolute' as const,
      left:      `${widgetRect.value.left}px`,
      top:       `${widgetRect.value.top}px`,
      width:     `${widgetRect.value.width}px`,
      height:    `${widgetRect.value.height}px`,
      boxShadow: shadow,
    }
  }
  return {
    position:    'absolute' as const,
    left:        '50%',
    top:         '50%',
    transform:   'translate(-50%, -50%)',
    width:       `min(calc(100vw - 2rem), calc((100vh - 8rem) * ${cfg.imageNaturalW} / ${cfg.imageNaturalH}))`,
    aspectRatio: `${cfg.imageNaturalW} / ${cfg.imageNaturalH}`,
    boxShadow:   shadow,
  }
})

function openFullscreen() {
  captureWidgetRect()
  placingMwId.value    = null
  fullscreenOpen.value = true
  drawingMode.value    = true
  currentPoints.value  = []
}

function closeFullscreen() {
  fullscreenOpen.value = false
  drawingMode.value    = false
  currentPoints.value  = []
  placingMwId.value    = null
  draggingMwId.value   = null
}

function onCanvasClick(e: MouseEvent) {
  if (!canvasRef.value || placingMwId.value) return
  const pos = getImageCoords(e, canvasRef.value)
  if (currentPoints.value.length >= 3 && isNearFirst(pos)) {
    finishArea()
    return
  }
  currentPoints.value.push(pos)
}

function onCanvasMouseMove(e: MouseEvent) {
  if (!canvasRef.value) return
  const pos = getImageCoords(e, canvasRef.value)
  if (draggingMwId.value) {
    const mw = cfg.miniWidgets.find(m => m.id === draggingMwId.value)
    if (mw) { mw.x = pos[0]; mw.y = pos[1] }
    return
  }
  mousePos.value = pos
}

function startMarkerDrag(e: MouseEvent, mwId: string) {
  e.stopPropagation()
  draggingMwId.value = mwId
}

function onCanvasMouseUp() {
  draggingMwId.value = null
}

// Live drawing preview
const livePoints = computed<Array<[number, number]>>(() =>
  drawingMode.value && currentPoints.value.length > 0
    ? [...currentPoints.value, mousePos.value]
    : []
)

const nearFirst = computed(() => drawingMode.value && isNearFirst(mousePos.value))

const drawingHint = computed(() => {
  const n = currentPoints.value.length
  if (n === 0) return 'Klicken um ersten Eckpunkt zu setzen'
  if (n < 3)   return `${n} Punkt${n > 1 ? 'e' : ''} — noch ${3 - n} bis Mindestpolygon`
  return nearFirst.value
    ? 'Auf den ersten Punkt klicken zum Schliessen ↩'
    : `${n} Punkte — Enter oder ersten Punkt zum Schliessen`
})

// Keyboard shortcuts (active while modal is open)
function onKeyDown(e: KeyboardEvent) {
  if (!fullscreenOpen.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    if (currentPoints.value.length > 0) { cancelCurrentPolygon() } else { closeFullscreen() }
  }
  if (e.key === 'Enter' && !placingMwId.value && currentPoints.value.length >= 3) {
    e.preventDefault()
    finishArea()
  }
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onUnmounted(() => document.removeEventListener('keydown', onKeyDown))

// ── Area selection & editing ──────────────────────────────────────────────────

const selectedAreaId = ref<string | null>(null)

const selectedArea = computed(() =>
  selectedAreaId.value ? cfg.areas.find(a => a.id === selectedAreaId.value) ?? null : null
)

function selectArea(id: string) {
  selectedAreaId.value = selectedAreaId.value === id ? null : id
  selectedMwId.value   = null
}

function deleteArea(id: string) {
  const idx = cfg.areas.findIndex(a => a.id === id)
  if (idx !== -1) cfg.areas.splice(idx, 1)
  if (selectedAreaId.value === id) selectedAreaId.value = null
}

// ── Page Picker (navigate action) ─────────────────────────────────────────────

const pagePickerOpen  = ref(false)
const pagePickerQuery = ref('')
const pagePickerInput = ref<HTMLInputElement>()

function nodePath(node: VisuNode): string {
  const parts: string[] = []
  let cur: VisuNode | undefined = node
  while (cur) {
    parts.unshift(cur.name)
    cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
  }
  return parts.join(' / ')
}

const filteredNodes = computed(() => {
  const q = pagePickerQuery.value.toLowerCase().trim()
  return store.nodes
    .map(n => ({ node: n, path: nodePath(n) }))
    .filter(({ path }) => !q || path.toLowerCase().includes(q))
    .sort((a, b) => a.path.localeCompare(b.path))
    .slice(0, 40)
})

async function openPagePicker() {
  if (!store.treeLoaded) await store.loadTree()
  pagePickerOpen.value  = true
  pagePickerQuery.value = ''
  nextTick(() => pagePickerInput.value?.focus())
}

function selectPage(id: string) {
  if (selectedArea.value) selectedArea.value.actionValue = id
  pagePickerOpen.value = false
}

function closePagePickerOnOutside() { pagePickerOpen.value = false }
onMounted(() => document.addEventListener('click', closePagePickerOnOutside))
onUnmounted(() => document.removeEventListener('click', closePagePickerOnOutside))

// ── Mini-widget management ─────────────────────────────────────────────────────

const selectedMwId = ref<string | null>(null)
const typePicker   = ref(false)

// All registerd widget types except Grundriss and WidgetRef (to avoid recursion / complexity)
const availableWidgetTypes = computed(() =>
  WidgetRegistry.all().filter(d => d.type !== 'Grundriss' && d.type !== 'WidgetRef')
)

function selectMw(id: string) {
  selectedMwId.value   = selectedMwId.value === id ? null : id
  selectedAreaId.value = null
}

function deleteMw(id: string) {
  const idx = cfg.miniWidgets.findIndex(m => m.id === id)
  if (idx !== -1) cfg.miniWidgets.splice(idx, 1)
  if (selectedMwId.value === id) selectedMwId.value = null
}

function addMiniWidget(type: string) {
  const def = WidgetRegistry.get(type)
  if (!def) return
  const mw: GrundrissMiniWidget = {
    id:                newId(),
    label:             def.label,
    widgetType:        type,
    config:            { ...def.defaultConfig },
    datapointId:       null,
    statusDatapointId: null,
    x:                 cfg.imageNaturalW / 2,
    y:                 cfg.imageNaturalH / 2,
    wPx:               120,
    hPx:               80,
    visible:           true,
  }
  cfg.miniWidgets.push(mw)
  selectedMwId.value   = mw.id
  selectedAreaId.value = null
  typePicker.value     = false
}

function updateMwConfig(id: string, newConfig: Record<string, unknown>) {
  const mw = cfg.miniWidgets.find(m => m.id === id)
  if (mw) mw.config = newConfig
}

// Mini-widget placement (drag on canvas)
const placingMwId  = ref<string | null>(null)
const draggingMwId = ref<string | null>(null)

const placingMw = computed(() =>
  placingMwId.value ? cfg.miniWidgets.find(m => m.id === placingMwId.value) ?? null : null
)

function openPlacement(mwId: string) {
  captureWidgetRect()
  placingMwId.value    = mwId
  fullscreenOpen.value = true
  drawingMode.value    = true
  currentPoints.value  = []
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- ══ Hintergrundbild ═══════════════════════════════════════════════════ -->
    <div>
      <p class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Hintergrundbild</p>

      <input ref="fileInput" type="file" accept="image/*,.svg" class="hidden" @change="onFileChange" />

      <button
        type="button"
        class="w-full py-1.5 text-xs rounded border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-500 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
        @click="triggerFileInput"
      >
        {{ cfg.image ? '↺ Bild ersetzen' : '+ Bild hochladen (SVG, PNG, JPG)' }}
      </button>

      <button
        v-if="cfg.image"
        type="button"
        class="w-full mt-1 py-0.5 text-xs text-red-600 dark:text-red-400 hover:text-red-500 dark:hover:text-red-300"
        @click="cfg.image = null"
      >Bild entfernen</button>

      <p v-if="cfg.image" class="text-xs text-gray-500 mt-0.5">
        {{ cfg.imageNaturalW }} × {{ cfg.imageNaturalH }} px
      </p>
      <p v-if="imageSizeWarn" class="text-xs text-amber-600 dark:text-amber-500 mt-0.5">
        Grosse Bilddatei — SVG empfohlen für optimale Performance.
      </p>
    </div>

    <!-- ══ Rotation ══════════════════════════════════════════════════════════ -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Rotation (in der Visu)</label>
      <div class="flex gap-1">
        <button
          v-for="deg in [0, 90, 180, 270]"
          :key="deg"
          type="button"
          :class="[
            'flex-1 py-1.5 text-xs rounded border',
            cfg.rotation === deg
              ? 'border-blue-500 bg-blue-500/20 text-blue-600 dark:text-blue-300'
              : 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-gray-400 dark:hover:border-gray-500',
          ]"
          @click="cfg.rotation = deg as 0|90|180|270"
        >{{ deg }}°</button>
      </div>
      <p class="text-xs text-gray-500 dark:text-gray-600 mt-0.5">
        Bereiche und Mini-Widgets werden auf dem unrotierten Originalbild gezeichnet bzw. platziert.
      </p>
    </div>

    <!-- ══ Bereichsnamen ═════════════════════════════════════════════════════ -->
    <div class="flex items-center gap-2">
      <input
        id="grnd-show-names"
        v-model="cfg.showAreaNames"
        type="checkbox"
        class="rounded border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 accent-blue-500"
      />
      <label for="grnd-show-names" class="text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
        Bereichsnamen anzeigen
      </label>
    </div>

    <!-- ══ Bereiche ══════════════════════════════════════════════════════════ -->
    <div>
      <p class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Bereiche</p>

      <!-- Open fullscreen drawing button -->
      <button
        type="button"
        :disabled="!cfg.image"
        class="w-full mt-1 py-1.5 text-xs rounded border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-500 hover:text-blue-500 dark:hover:text-blue-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        @click="openFullscreen"
      >
        ✏ Bereiche zeichnen (Vollbild)
      </button>
    </div>

    <!-- ══ Bereichsliste ═════════════════════════════════════════════════════ -->
    <div v-if="cfg.areas.length > 0" class="space-y-1">
      <p class="text-xs text-gray-500">{{ cfg.areas.length }} Bereich(e) — Klicken zum Bearbeiten</p>

      <div
        v-for="area in cfg.areas"
        :key="area.id"
        class="border rounded overflow-hidden"
        :class="area.id === selectedAreaId ? 'border-blue-500 dark:border-blue-600' : 'border-gray-200 dark:border-gray-700'"
      >
        <!-- Header row -->
        <div
          class="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer"
          :class="area.id === selectedAreaId ? 'bg-blue-500/10' : 'hover:bg-gray-100 dark:hover:bg-gray-800'"
          @click="selectArea(area.id)"
        >
          <span class="text-xs font-medium text-gray-800 dark:text-gray-200 flex-1 truncate min-w-0">
            {{ area.name || '(kein Name)' }}
          </span>
          <span class="text-xs text-gray-400 shrink-0">{{ area.points.length }}P</span>
          <span
            v-if="area.actionType !== 'none'"
            class="text-xs text-blue-500 dark:text-blue-400 shrink-0"
            title="Navigation konfiguriert"
          >↗</span>
          <button
            type="button"
            class="text-xs text-gray-400 hover:text-red-500 dark:hover:text-red-400 px-0.5 shrink-0"
            title="Bereich löschen"
            @click.stop="deleteArea(area.id)"
          >✕</button>
        </div>

        <!-- Expanded details -->
        <div
          v-if="area.id === selectedAreaId"
          class="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-2 space-y-2.5"
        >
          <!-- Name -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Name</label>
            <input
              v-model="area.name"
              type="text"
              placeholder="z.B. Wohnzimmer"
              class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>

          <!-- Label -->
          <div>
            <div class="flex items-center gap-2">
              <input
                :id="`grnd-lbl-${area.id}`"
                v-model="area.showLabel"
                type="checkbox"
                class="rounded border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 accent-blue-500"
              />
              <label :for="`grnd-lbl-${area.id}`" class="text-xs text-gray-700 dark:text-gray-300 cursor-pointer flex-1">
                Name einblenden
              </label>
              <input
                v-if="area.showLabel"
                v-model="area.labelColor"
                type="color"
                class="w-6 h-6 rounded cursor-pointer border border-gray-300 dark:border-gray-700 bg-transparent p-0.5 shrink-0"
                title="Textfarbe"
              />
            </div>
            <div v-if="area.showLabel" class="grid grid-cols-2 gap-1 mt-1.5">
              <div>
                <label class="block text-xs text-gray-500 mb-0.5">Name X (px)</label>
                <input
                  v-model.number="area.labelX"
                  type="number" :min="0" :max="cfg.imageNaturalW" step="1"
                  class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label class="block text-xs text-gray-500 mb-0.5">Name Y (px)</label>
                <input
                  v-model.number="area.labelY"
                  type="number" :min="0" :max="cfg.imageNaturalH" step="1"
                  class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          <!-- Aktion -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Aktion bei Klick</label>
            <div class="flex gap-1">
              <button
                v-for="at in [{ v: 'none', l: 'Keine' }, { v: 'navigate', l: 'Navigation' }]"
                :key="at.v"
                type="button"
                :class="[
                  'flex-1 py-1 text-xs rounded border',
                  area.actionType === at.v
                    ? 'border-blue-500 bg-blue-500/20 text-blue-600 dark:text-blue-300'
                    : 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-gray-400 dark:hover:border-gray-500',
                ]"
                @click="area.actionType = at.v as 'none' | 'navigate'"
              >{{ at.l }}</button>
            </div>
          </div>

          <!-- Page picker -->
          <div v-if="area.actionType === 'navigate'">
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Ziel-Seite</label>
            <div v-if="!pagePickerOpen" class="flex items-center gap-1">
              <div
                class="flex-1 flex items-center gap-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 cursor-pointer hover:border-gray-400 dark:hover:border-gray-500 transition-colors overflow-hidden"
                @click.stop="openPagePicker"
              >
                <span
                  class="text-xs truncate"
                  :class="area.actionValue ? 'text-gray-800 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500'"
                >
                  {{ area.actionValue
                      ? (store.getNode(area.actionValue)
                          ? nodePath(store.getNode(area.actionValue)!)
                          : area.actionValue)
                      : 'Seite wählen …'
                  }}
                </span>
                <span class="ml-auto text-gray-400 text-xs shrink-0">▾</span>
              </div>
              <button
                v-if="area.actionValue"
                type="button"
                class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 px-1 shrink-0"
                @click="area.actionValue = ''"
              >✕</button>
            </div>
            <div
              v-else
              class="border border-blue-500 rounded bg-white dark:bg-gray-800 overflow-hidden"
              @click.stop
            >
              <input
                ref="pagePickerInput"
                v-model="pagePickerQuery"
                type="text"
                placeholder="Seitenname suchen …"
                class="w-full bg-transparent px-2 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none"
                @keydown.escape="pagePickerOpen = false"
              />
              <div class="max-h-40 overflow-y-auto border-t border-gray-200 dark:border-gray-700">
                <div v-if="filteredNodes.length === 0" class="text-xs text-gray-400 px-2 py-1.5">
                  Keine Treffer
                </div>
                <button
                  v-for="{ node, path } in filteredNodes"
                  :key="node.id"
                  type="button"
                  class="w-full flex items-center gap-1.5 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 text-left transition-colors"
                  :class="node.id === area.actionValue ? 'bg-blue-500/10' : ''"
                  @click="selectPage(node.id)"
                >
                  <span class="flex-1 text-xs text-gray-800 dark:text-gray-200 truncate">{{ path }}</span>
                  <span class="text-xs text-gray-400 shrink-0">
                    {{ node.type === 'PAGE' ? 'Seite' : 'Bereich' }}
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <p v-else-if="cfg.image" class="text-xs text-gray-500 dark:text-gray-600 italic">
      Noch keine Bereiche — „Bereiche zeichnen" klicken.
    </p>

    <!-- ══ Mini-Widgets ══════════════════════════════════════════════════════ -->
    <div>
      <p class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Mini-Widgets</p>

      <!-- List -->
      <div v-if="cfg.miniWidgets.length > 0" class="space-y-1 mb-2">
        <div
          v-for="mw in cfg.miniWidgets"
          :key="mw.id"
          class="border rounded overflow-hidden"
          :class="mw.id === selectedMwId ? 'border-red-400 dark:border-red-600' : 'border-gray-200 dark:border-gray-700'"
        >
          <!-- Header row -->
          <div
            class="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer"
            :class="mw.id === selectedMwId ? 'bg-red-500/10' : 'hover:bg-gray-100 dark:hover:bg-gray-800'"
            @click="selectMw(mw.id)"
          >
            <span class="text-sm w-5 text-center leading-none" v-html="WidgetRegistry.get(mw.widgetType)?.icon ?? '?'" />
            <span class="text-xs font-medium text-gray-800 dark:text-gray-200 flex-1 truncate min-w-0">
              {{ mw.label || mw.widgetType }}
            </span>
            <!-- Visibility indicator -->
            <button
              type="button"
              :class="['text-xs shrink-0', mw.visible ? 'text-green-500' : 'text-gray-400 dark:text-gray-600']"
              :title="mw.visible ? 'Sichtbar — klicken zum Ausblenden' : 'Ausgeblendet — klicken zum Einblenden'"
              @click.stop="mw.visible = !mw.visible"
            >{{ mw.visible ? '◉' : '○' }}</button>
            <!-- Delete -->
            <button
              type="button"
              class="text-xs text-gray-400 hover:text-red-500 dark:hover:text-red-400 px-0.5 shrink-0"
              title="Mini-Widget löschen"
              @click.stop="deleteMw(mw.id)"
            >✕</button>
          </div>

          <!-- Expanded details -->
          <div
            v-if="mw.id === selectedMwId"
            class="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-2 space-y-2.5"
          >
            <!-- Label -->
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Bezeichnung</label>
              <input
                v-model="mw.label"
                type="text"
                placeholder="z.B. Lampe Flur"
                class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>

            <!-- Widget type badge -->
            <div class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
              <span v-html="WidgetRegistry.get(mw.widgetType)?.icon ?? '?'" />
              <span>{{ WidgetRegistry.get(mw.widgetType)?.label ?? mw.widgetType }}</span>
            </div>

            <!-- Datenpunkt -->
            <DataPointPicker
              :model-value="mw.datapointId"
              label="Datenpunkt"
              :compatible-types="['*']"
              @update:model-value="(v) => mw.datapointId = v"
            />

            <!-- Status-Datenpunkt (only for widgets that support a separate status DP) -->
            <DataPointPicker
              v-if="WidgetRegistry.get(mw.widgetType)?.supportsStatusDatapoint"
              :model-value="mw.statusDatapointId"
              label="Status-Datenpunkt (optional)"
              :compatible-types="['*']"
              @update:model-value="(v) => mw.statusDatapointId = v"
            />

            <!-- Size -->
            <div class="grid grid-cols-2 gap-1">
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Breite (px)</label>
                <input
                  v-model.number="mw.wPx"
                  type="number" min="40" max="400" step="10"
                  class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-0.5">Höhe (px)</label>
                <input
                  v-model.number="mw.hPx"
                  type="number" min="40" max="400" step="10"
                  class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-1.5 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <!-- Sichtbar -->
            <div class="flex items-center gap-2">
              <input
                :id="`mw-vis-${mw.id}`"
                v-model="mw.visible"
                type="checkbox"
                class="rounded border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 accent-blue-500"
              />
              <label :for="`mw-vis-${mw.id}`" class="text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
                In Visu anzeigen
              </label>
            </div>

            <!-- Place on map -->
            <button
              type="button"
              :disabled="!cfg.image"
              class="w-full py-1.5 text-xs rounded border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-500 hover:text-blue-500 dark:hover:text-blue-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              @click="openPlacement(mw.id)"
            >
              📍 Position setzen (Vollbild)
            </button>

            <!-- Embedded widget config -->
            <div v-if="WidgetRegistry.get(mw.widgetType)?.configComponent">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1.5">Widget-Konfiguration</p>
              <div class="rounded border border-gray-200 dark:border-gray-700 p-2">
                <component
                  :is="WidgetRegistry.get(mw.widgetType)!.configComponent"
                  :model-value="mw.config"
                  @update:model-value="(v: Record<string, unknown>) => updateMwConfig(mw.id, v)"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <p v-else class="text-xs text-gray-500 dark:text-gray-600 italic mb-2">
        Noch keine Mini-Widgets.
      </p>

      <!-- Add button / type picker -->
      <div v-if="typePicker" class="border border-blue-500 rounded-xl p-2.5 bg-white dark:bg-gray-900 space-y-2">
        <p class="text-xs font-semibold text-gray-700 dark:text-gray-300">Widget-Typ wählen</p>
        <div class="grid grid-cols-3 gap-1.5 max-h-52 overflow-y-auto">
          <button
            v-for="def in availableWidgetTypes"
            :key="def.type"
            type="button"
            class="flex flex-col items-center gap-0.5 p-2 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-colors"
            @click="addMiniWidget(def.type)"
          >
            <span class="text-base leading-none" v-html="def.icon" />
            <span class="text-xs text-gray-700 dark:text-gray-300 text-center leading-tight mt-0.5">{{ def.label }}</span>
          </button>
        </div>
        <button
          type="button"
          class="w-full text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 py-0.5"
          @click="typePicker = false"
        >Abbrechen</button>
      </div>

      <button
        v-else
        type="button"
        :disabled="!cfg.image"
        class="w-full py-1.5 text-xs rounded border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-500 hover:text-blue-500 dark:hover:text-blue-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        @click="typePicker = true"
      >
        + Mini-Widget hinzufügen
      </button>
    </div>

  </div>

  <!-- ══ Vollbild-Modal (Bereiche zeichnen + Mini-Widget platzieren) ══════════ -->
  <Teleport to="body">
    <div
      v-if="fullscreenOpen"
      class="fixed inset-0 z-50 pointer-events-none"
    >
      <!-- Drawing / placement canvas — positioned over the widget, shadow dims the rest -->
      <div
        ref="canvasRef"
        class="relative select-none pointer-events-auto"
        :style="[canvasPositionStyle, { cursor: draggingMwId ? 'grabbing' : 'crosshair' }]"
        @click="onCanvasClick"
        @mousemove="onCanvasMouseMove"
        @mouseup="onCanvasMouseUp"
      >

        <!-- SVG overlay -->
        <svg
          class="absolute inset-0 w-full h-full"
          :viewBox="svgViewBox"
          preserveAspectRatio="xMidYMid meet"
        >
            <!-- Existing areas -->
            <g v-for="area in cfg.areas" :key="area.id">
              <polygon
                :points="ptStr(area.points)"
                fill="rgba(59,130,246,0.12)"
                stroke="#3b82f6"
                :stroke-width="strokeW"
              />
              <text
                v-if="area.showLabel"
                :x="area.labelX" :y="area.labelY"
                text-anchor="middle" dominant-baseline="middle"
                :font-size="fontSize" :fill="area.labelColor || '#ffffff'"
                stroke="rgba(0,0,0,0.65)" :stroke-width="fontSize * 0.18"
                paint-order="stroke fill"
                style="pointer-events: none; user-select: none;"
              >{{ area.name }}</text>
            </g>

            <!-- Mini-widget position markers (draggable) -->
            <g v-for="mw in cfg.miniWidgets" :key="`fs-mw-${mw.id}`">
              <circle
                :cx="mw.x" :cy="mw.y"
                :r="cfg.imageNaturalW * 0.013"
                :fill="mw.id === placingMwId || mw.id === draggingMwId ? 'rgba(239,68,68,0.9)' : 'rgba(239,68,68,0.5)'"
                :stroke="mw.id === placingMwId || mw.id === draggingMwId ? '#fff' : '#ef4444'"
                :stroke-width="strokeW"
                :style="{ cursor: draggingMwId === mw.id ? 'grabbing' : 'grab' }"
                @mousedown.stop="startMarkerDrag($event, mw.id)"
              />
              <text
                :x="mw.x" :y="mw.y + cfg.imageNaturalW * 0.02"
                text-anchor="middle" dominant-baseline="hanging"
                :font-size="fontSize * 0.55"
                :fill="mw.id === placingMwId || mw.id === draggingMwId ? '#fff' : '#fca5a5'"
                style="pointer-events: none; user-select: none;"
              >{{ mw.label || mw.widgetType }}</text>
            </g>

            <!-- Live drawing preview -->
            <g v-if="!placingMwId && livePoints.length > 0">
              <polygon
                v-if="livePoints.length >= 3"
                :points="ptStr(livePoints)"
                fill="rgba(251,191,36,0.12)"
                stroke="#fbbf24"
                :stroke-width="strokeW"
                stroke-dasharray="4,2"
              />
              <line
                v-else-if="livePoints.length === 2"
                :x1="livePoints[0][0]" :y1="livePoints[0][1]"
                :x2="livePoints[1][0]" :y2="livePoints[1][1]"
                stroke="#fbbf24"
                :stroke-width="strokeW"
              />
              <circle
                v-if="nearFirst"
                :cx="currentPoints[0][0]" :cy="currentPoints[0][1]"
                :r="closeThreshold * 0.75"
                fill="rgba(34,197,94,0.15)" stroke="#22c55e"
                :stroke-width="strokeW * 0.8"
              />
              <circle
                v-for="(pt, i) in currentPoints"
                :key="i"
                :cx="pt[0]" :cy="pt[1]"
                :r="i === 0 ? strokeW * 3.5 : strokeW * 2"
                :fill="i === 0 && nearFirst ? '#22c55e' : '#fbbf24'"
                stroke="white" :stroke-width="strokeW * 0.5"
              />
            </g>
          </svg>
      </div>

      <!-- Toolbar — above the dimming shadow -->
      <div class="absolute top-0 left-0 right-0 flex items-center gap-3 px-4 py-2.5 bg-gray-900 border-b border-gray-700 pointer-events-auto">

        <span class="text-sm font-semibold text-gray-100">
          {{ placingMwId ? 'Mini-Widget positionieren' : 'Bereiche zeichnen' }}
        </span>
        <span class="text-xs text-gray-400 flex-1 min-w-0 truncate">
          {{ placingMwId
            ? `„${placingMw?.label || placingMw?.widgetType}" auf die gewünschte Position ziehen`
            : drawingHint
          }}
        </span>

        <template v-if="!placingMwId">
          <button
            type="button"
            :disabled="currentPoints.length < 3"
            class="py-1 px-3 text-xs rounded border border-amber-600 text-amber-300 hover:border-amber-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
            @click="finishArea"
          >Polygon abschliessen ↩</button>

          <button
            type="button"
            :disabled="currentPoints.length === 0"
            class="py-1 px-3 text-xs rounded border border-gray-600 text-gray-300 hover:border-gray-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
            @click="cancelCurrentPolygon"
          >Aktuelles verwerfen Esc</button>
        </template>

        <button
          type="button"
          class="py-1 px-4 text-xs rounded bg-blue-600 hover:bg-blue-500 text-white transition-colors shrink-0"
          @click="closeFullscreen"
        >Fertig</button>
      </div>

      <!-- Status bar — above the dimming shadow -->
      <div class="absolute bottom-0 left-0 right-0 flex items-center justify-between px-4 py-2 bg-gray-900/80 border-t border-gray-800 text-xs text-gray-400 pointer-events-auto">
        <span>{{ cfg.areas.length }} Bereich(e) · {{ cfg.miniWidgets.length }} Mini-Widget(s)</span>
        <span v-if="placingMwId">Marker ziehen · Fertig zum Schliessen</span>
        <span v-else>Enter = Polygon schliessen · Esc = verwerfen · Klick auf ersten Punkt = schliessen</span>
      </div>
    </div>
  </Teleport>
</template>

