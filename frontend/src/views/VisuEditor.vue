<script setup lang="ts">
/**
 * VisuEditor — Drag & Drop Page Editor (Phase 5)
 *
 * Verwendet Gridstack.js für das Layout.
 * Aktuell: Grundgerüst mit Widget-Palette, Config-Panel und Speichern.
 */
import { computed, onMounted, onUnmounted, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { WidgetRegistry } from '@/widgets/registry'
import WidgetPalette from '@/components/WidgetPalette.vue'
import Breadcrumb from '@/components/Breadcrumb.vue'
import type { PageConfig, WidgetInstance } from '@/types'

// Widget-Registrierungen laden
import '@/widgets/ValueDisplay/index'
import '@/widgets/Toggle/index'
import '@/widgets/Slider/index'
import '@/widgets/Chart/index'
import '@/widgets/Link/index'

import { GridStack } from 'gridstack'
import 'gridstack/dist/gridstack.min.css'

const props = defineProps<{ id: string }>()
const router = useRouter()
const store = useVisuStore()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const selectedWidget = ref<WidgetInstance | null>(null)

// Lokale Kopie der Page-Config (wird erst beim Speichern übermittelt)
const config = ref<PageConfig>({
  grid_cols: 12,
  grid_row_height: 80,
  background: null,
  widgets: [],
})

let grid: GridStack | null = null
const gridEl = ref<HTMLElement | null>(null)

onMounted(async () => {
  try {
    if (!store.treeLoaded) await store.loadTree()
    await store.loadBreadcrumb(props.id)
    await store.loadPage(props.id)
    if (store.pageConfig) config.value = JSON.parse(JSON.stringify(store.pageConfig))
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden'
  } finally {
    loading.value = false
  }

  await nextTick()
  if (gridEl.value) {
    grid = GridStack.init(
      {
        column: config.value.grid_cols,
        cellHeight: config.value.grid_row_height,
        margin: 4,
        animate: false,
        resizable: { handles: 'se' },
      },
      gridEl.value
    )

    grid.on('change', () => syncFromGrid())
  }
})

onUnmounted(() => {
  grid?.destroy()
})

function syncFromGrid() {
  if (!grid) return
  const items = grid.getGridItems()
  config.value.widgets = items.map((el) => {
    const node = grid!.getNodeData(el)
    const existing = config.value.widgets.find((w) => w.id === el.dataset.id)
    return {
      ...(existing ?? { id: el.dataset.id!, type: 'ValueDisplay', datapoint_id: null, config: {} }),
      x: node?.x ?? 0,
      y: node?.y ?? 0,
      w: node?.w ?? 2,
      h: node?.h ?? 2,
    }
  })
}

function insertWidget(type: string) {
  const def = WidgetRegistry.get(type)
  if (!def || !grid) return
  const id = crypto.randomUUID()
  const w: WidgetInstance = {
    id,
    type,
    datapoint_id: null,
    x: 0, y: 0,
    w: def.defaultW, h: def.defaultH,
    config: { ...def.defaultConfig },
  }
  config.value.widgets.push(w)
  grid.addWidget({
    x: w.x, y: w.y, w: w.w, h: w.h,
    minW: def.minW, minH: def.minH,
    id,
    content: `<div class="gs-widget-placeholder text-xs text-gray-400 p-2">${def.label}</div>`,
  })
  selectedWidget.value = w
}

function selectWidget(w: WidgetInstance) {
  selectedWidget.value = selectedWidget.value?.id === w.id ? null : w
}

function updateWidgetConfig(newConfig: Record<string, unknown>) {
  if (!selectedWidget.value) return
  const w = config.value.widgets.find((x) => x.id === selectedWidget.value!.id)
  if (w) w.config = newConfig
}

function removeSelected() {
  if (!selectedWidget.value || !grid) return
  const el = gridEl.value?.querySelector(`[data-id="${selectedWidget.value.id}"]`)
  if (el) grid.removeWidget(el as HTMLElement)
  config.value.widgets = config.value.widgets.filter((w) => w.id !== selectedWidget.value!.id)
  selectedWidget.value = null
}

async function save() {
  saving.value = true
  try {
    await store.savePage(props.id, config.value)
    router.push({ name: 'viewer', params: { id: props.id } })
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}

const selectedWidgetDef = computed(() =>
  selectedWidget.value ? WidgetRegistry.get(selectedWidget.value.type) : null
)
</script>

<template>
  <div class="h-screen flex flex-col bg-gray-950 text-gray-100 overflow-hidden">
    <!-- Toolbar -->
    <header class="border-b border-gray-800 px-4 py-2 flex items-center gap-4 flex-shrink-0">
      <Breadcrumb />
      <div class="flex-1" />
      <span class="text-xs text-gray-500">Editor</span>
      <button
        class="text-sm text-gray-400 hover:text-gray-200 transition-colors px-3 py-1.5 rounded"
        @click="router.push({ name: 'viewer', params: { id } })"
      >
        Abbrechen
      </button>
      <button
        class="text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg transition-colors"
        :disabled="saving"
        @click="save"
      >
        {{ saving ? 'Speichere …' : '💾 Speichern' }}
      </button>
    </header>

    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-500">Lade …</div>
    <div v-else-if="error" class="flex-1 flex items-center justify-center text-red-400">{{ error }}</div>

    <div v-else class="flex-1 flex min-h-0">
      <!-- Widget-Palette (links) -->
      <WidgetPalette @insert="insertWidget" />

      <!-- Grid-Canvas (Mitte) -->
      <div class="flex-1 overflow-auto bg-gray-950 p-4">
        <div ref="gridEl" class="grid-stack" />
      </div>

      <!-- Config-Panel (rechts) -->
      <aside class="w-72 flex-shrink-0 bg-gray-900 border-l border-gray-700 overflow-y-auto p-4">
        <template v-if="selectedWidget && selectedWidgetDef">
          <div class="flex items-center justify-between mb-4">
            <span class="text-sm font-semibold text-gray-200">
              {{ selectedWidgetDef.label }}
            </span>
            <button
              class="text-xs text-red-400 hover:text-red-300 transition-colors"
              @click="removeSelected"
            >
              🗑 Entfernen
            </button>
          </div>

          <!-- DataPoint-Auswahl -->
          <div class="mb-4">
            <label class="block text-xs text-gray-400 mb-1">DataPoint-ID</label>
            <input
              :value="selectedWidget.datapoint_id ?? ''"
              type="text"
              placeholder="UUID des DataPoints"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-100 font-mono focus:outline-none focus:border-blue-500"
              @input="selectedWidget!.datapoint_id = ($event.target as HTMLInputElement).value || null"
            />
          </div>

          <hr class="border-gray-700 mb-4" />

          <!-- Widget-spezifisches Config-Formular -->
          <component
            :is="selectedWidgetDef.configComponent"
            :model-value="selectedWidget.config"
            @update:model-value="updateWidgetConfig"
          />
        </template>
        <div v-else class="text-sm text-gray-600 text-center mt-8">
          Widget auswählen oder aus der Palette einfügen
        </div>
      </aside>
    </div>
  </div>
</template>

<style>
.grid-stack-item-content {
  @apply bg-gray-800 border border-gray-700 rounded-xl overflow-hidden cursor-pointer;
}
</style>
