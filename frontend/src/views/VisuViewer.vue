<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useDatapointsStore } from '@/stores/datapoints'
import { useWebSocket } from '@/composables/useWebSocket'
import { useThemeStore } from '@/stores/theme'
import { WidgetRegistry } from '@/widgets/registry'
import Breadcrumb from '@/components/Breadcrumb.vue'
import NodeOverview from '@/components/NodeOverview.vue'
import AuthButton from '@/components/AuthButton.vue'
import { getJwt, getSessionToken, setWriteContext, clearWriteContext } from '@/api/client'
import type { WidgetInstance } from '@/types'

// Alle Widgets registrieren (self-registering via import)
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

const props = defineProps<{ id: string }>()
const router = useRouter()
const visuStore = useVisuStore()
const dpStore = useDatapointsStore()
const ws = useWebSocket()
const theme = useThemeStore()

const loading = ref(true)
const error = ref('')

const node = computed(() => visuStore.getNode(props.id))
const isPage = computed(() => node.value?.type === 'PAGE')

/** Effektiven Zugangslevel ermitteln und den definierenden Knoten zurückgeben */
function resolveAccessNode(nodeId: string): { access: string; definingId: string } {
  let cur = visuStore.getNode(nodeId)
  while (cur) {
    if (cur.access !== null) return { access: cur.access, definingId: cur.id }
    cur = cur.parent_id ? visuStore.getNode(cur.parent_id) : undefined
  }
  return { access: 'public', definingId: nodeId }
}

/** Nur-Lesen-Modus: Seite hat access='readonly' (effektiv) */
const isReadOnly = computed(() => resolveAccessNode(props.id).access === 'readonly')
const widgets = computed<WidgetInstance[]>(() => visuStore.pageConfig?.widgets ?? [])

// Haupt-Datenpunkt-IDs
const datapointIds = computed(() =>
  widgets.value.map((w) => w.datapoint_id).filter((id): id is string => !!id)
)

// Status-Datenpunkt-IDs (separater Rückmelde-DP)
const statusDpIds = computed(() =>
  widgets.value.map((w) => w.status_datapoint_id).filter((id): id is string => !!id)
)

// Extra-Datenpunkt-IDs aus Widget-Configs (z.B. Info-Widget)
const extraDpIds = computed(() => {
  const ids: string[] = []
  for (const w of widgets.value) {
    const def = WidgetRegistry.get(w.type)
    if (def?.getExtraDatapointIds) {
      ids.push(...def.getExtraDatapointIds(w.config))
    }
  }
  return ids
})

// Alle zu abonnierenden IDs (Haupt + Status + Extra, dedupliziert)
const allDpIds = computed(() => {
  const set = new Set([...datapointIds.value, ...statusDpIds.value, ...extraDpIds.value])
  return Array.from(set)
})

watch(allDpIds, (newIds, oldIds) => {
  const added = newIds.filter((id) => !oldIds?.includes(id))
  const removed = (oldIds ?? []).filter((id) => !newIds.includes(id))
  if (added.length) {
    dpStore.subscribe(added)
    dpStore.fetchInitialValues(added)
  }
  if (removed.length) dpStore.unsubscribe(removed)
}, { immediate: false })

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (!visuStore.treeLoaded) await visuStore.loadTree()
    await visuStore.loadBreadcrumb(props.id)

    const currentNode = visuStore.getNode(props.id)
    const { access: effectiveAccess, definingId } = resolveAccessNode(props.id)

    // Access-Check: protected → PIN-Auth (Vererbung berücksichtigen)
    if (effectiveAccess === 'protected' && !visuStore.hasSessionToken(definingId)) {
      router.push({ name: 'pin-auth', params: { id: definingId } })
      return
    }

    // Access-Check: user → JWT-Login erforderlich
    if (effectiveAccess === 'user' && !getJwt()) {
      router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
      return
    }

    if (currentNode?.type === 'PAGE') {
      await visuStore.loadPage(props.id)
      // Write-Kontext für Backend-Autorisierung setzen (pageId + ggf. Session-Token)
      setWriteContext({
        pageId:       props.id,
        sessionToken: getSessionToken(definingId) ?? undefined,
      })
      ws.connect()
      dpStore.subscribe(allDpIds.value)
      // Sofort aktuelle Werte per HTTP laden (unabhängig von WS-Status)
      await dpStore.fetchInitialValues(allDpIds.value)
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden'
  } finally {
    loading.value = false
  }
}

onMounted(load)
onUnmounted(clearWriteContext)
watch(() => props.id, load)

// Grid-Geometrie — feste Pixel-Werte → 1:1 identisch mit Editor (WYSIWYG)
const COLS   = computed(() => visuStore.pageConfig?.grid_cols       ?? 12)
const ROW_H  = computed(() => visuStore.pageConfig?.grid_row_height ?? 80)
const CELL_W = computed(() => visuStore.pageConfig?.grid_cell_width ?? 80)

function gridStyle(w: WidgetInstance) {
  return {
    gridColumn: `${w.x + 1} / span ${w.w}`,
    gridRow:    `${w.y + 1} / span ${w.h}`,
    height:     `${w.h * ROW_H.value}px`,
  }
}
</script>

<template>
  <div class="min-h-screen bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 flex flex-col">
    <!-- Header -->
    <header class="border-b border-gray-200 dark:border-gray-800 px-6 py-3 flex items-center justify-between gap-4 flex-shrink-0 bg-gray-50 dark:bg-gray-900">
      <Breadcrumb />
      <div class="flex items-center gap-2">
        <button
          v-if="visuStore.isAdmin && isPage"
          class="text-xs text-gray-400 dark:text-gray-500 hover:text-blue-500 dark:hover:text-blue-400 transition-colors px-2 py-1 rounded"
          @click="router.push({ name: 'editor', params: { id } })"
        >✏️ Bearbeiten</button>
        <button
          v-if="visuStore.isAdmin"
          class="text-xs text-gray-400 dark:text-gray-500 hover:text-blue-500 dark:hover:text-blue-400 transition-colors px-2 py-1 rounded"
          @click="router.push({ name: 'manage' })"
        >🗂 Visu Verwaltung</button>
        <!-- Hell/Dunkel-Umschalter -->
        <button
          class="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors px-2 py-1 rounded"
          :title="theme.isDark ? 'Heller Modus' : 'Dunkler Modus'"
          @click="theme.toggle()"
        >{{ theme.isDark ? '☀️' : '🌙' }}</button>
        <AuthButton />
      </div>
    </header>

    <!-- Loading -->
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
      Lade …
    </div>

    <!-- Error -->
    <div v-else-if="error" class="flex-1 flex items-center justify-center text-red-500 dark:text-red-400">
      {{ error }}
    </div>

    <!-- LOCATION → Auto-Übersicht -->
    <main v-else-if="!isPage" class="flex-1 max-w-5xl mx-auto w-full px-6 py-8">
      <h1 class="text-xl font-semibold mb-6">{{ node?.name }}</h1>
      <NodeOverview :node-id="id" />
    </main>

    <!-- PAGE → Widget-Grid (feste Pixelbreite = Editor WYSIWYG) -->
    <main v-else class="flex-1 p-4 overflow-auto">
      <div
        class="grid"
        :style="{
          gridTemplateColumns: `repeat(${COLS}, ${CELL_W}px)`,
          gridAutoRows: `${ROW_H}px`,
          width: `${COLS * CELL_W}px`,
        }"
      >
        <div
          v-for="w in widgets"
          :key="w.id"
          class="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden"
          :style="gridStyle(w)"
          :data-dp="w.datapoint_id"
          :data-widget-id="w.id"
        >
          <component
            :is="WidgetRegistry.get(w.type)?.component"
            v-if="WidgetRegistry.get(w.type)"
            :config="w.config"
            :datapoint-id="w.datapoint_id"
            :value="w.datapoint_id ? dpStore.getValue(w.datapoint_id) : null"
            :status-value="w.status_datapoint_id ? dpStore.getValue(w.status_datapoint_id) : null"
            :editor-mode="false"
            :readonly="isReadOnly"
            :h="w.h"
          />
          <div v-else class="flex items-center justify-center h-full text-gray-400 dark:text-gray-600 text-xs">
            Unbekannter Widget-Typ: {{ w.type }}
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
