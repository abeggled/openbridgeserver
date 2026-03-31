<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useDatapointsStore } from '@/stores/datapoints'
import { useWebSocket } from '@/composables/useWebSocket'
import { WidgetRegistry } from '@/widgets/registry'
import Breadcrumb from '@/components/Breadcrumb.vue'
import NodeOverview from '@/components/NodeOverview.vue'
import { getJwt } from '@/api/client'
import type { WidgetInstance } from '@/types'

// Alle Widgets registrieren (self-registering via import)
import '@/widgets/ValueDisplay/index'
import '@/widgets/Toggle/index'
import '@/widgets/Slider/index'
import '@/widgets/Chart/index'
import '@/widgets/Link/index'

const props = defineProps<{ id: string }>()
const router = useRouter()
const visuStore = useVisuStore()
const dpStore = useDatapointsStore()
const ws = useWebSocket()

const loading = ref(true)
const error = ref('')

const node = computed(() => visuStore.getNode(props.id))
const isPage = computed(() => node.value?.type === 'PAGE')
const widgets = computed<WidgetInstance[]>(() => visuStore.pageConfig?.widgets ?? [])

// DataPoint-IDs aller Widgets auf dieser Seite abonnieren
const datapointIds = computed(() =>
  widgets.value.map((w) => w.datapoint_id).filter((id): id is string => !!id)
)

watch(datapointIds, (newIds, oldIds) => {
  const added = newIds.filter((id) => !oldIds?.includes(id))
  const removed = (oldIds ?? []).filter((id) => !newIds.includes(id))
  if (added.length) dpStore.subscribe(added)
  if (removed.length) dpStore.unsubscribe(removed)
}, { immediate: false })

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (!visuStore.treeLoaded) await visuStore.loadTree()
    await visuStore.loadBreadcrumb(props.id)

    const currentNode = visuStore.getNode(props.id)

    // Access-Check: protected → PIN-Auth
    if (currentNode?.access === 'protected' && !visuStore.hasSessionToken(props.id)) {
      router.push({ name: 'pin-auth', params: { id: props.id } })
      return
    }

    // Access-Check: private → JWT
    if (currentNode?.access === 'private' && !getJwt()) {
      router.push({ name: 'tree' })
      return
    }

    if (currentNode?.type === 'PAGE') {
      await visuStore.loadPage(props.id)
      // Initial subscribe
      dpStore.subscribe(datapointIds.value)
      ws.connect()
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.id, load)

// Grid-Geometrie
const COLS = computed(() => visuStore.pageConfig?.grid_cols ?? 12)
const ROW_H = computed(() => visuStore.pageConfig?.grid_row_height ?? 80)

function gridStyle(w: WidgetInstance) {
  return {
    gridColumn: `${w.x + 1} / span ${w.w}`,
    gridRow: `${w.y + 1} / span ${w.h}`,
    height: `${w.h * ROW_H.value}px`,
  }
}
</script>

<template>
  <div class="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
    <!-- Header -->
    <header class="border-b border-gray-800 px-6 py-3 flex items-center justify-between gap-4 flex-shrink-0">
      <Breadcrumb />
      <div class="flex items-center gap-2">
        <button
          v-if="getJwt() && isPage"
          class="text-xs text-gray-500 hover:text-blue-400 transition-colors px-2 py-1 rounded"
          @click="router.push({ name: 'editor', params: { id } })"
        >
          ✏️ Bearbeiten
        </button>
      </div>
    </header>

    <!-- Loading -->
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-500">
      Lade …
    </div>

    <!-- Error -->
    <div v-else-if="error" class="flex-1 flex items-center justify-center text-red-400">
      {{ error }}
    </div>

    <!-- LOCATION → Auto-Übersicht -->
    <main v-else-if="!isPage" class="flex-1 max-w-5xl mx-auto w-full px-6 py-8">
      <h1 class="text-xl font-semibold text-gray-100 mb-6">{{ node?.name }}</h1>
      <NodeOverview :node-id="id" />
    </main>

    <!-- PAGE → Widget-Grid -->
    <main v-else class="flex-1 p-4 overflow-auto">
      <div
        class="grid gap-2"
        :style="{
          gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))`,
          gridAutoRows: `${ROW_H}px`,
        }"
      >
        <div
          v-for="w in widgets"
          :key="w.id"
          class="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden"
          :style="gridStyle(w)"
        >
          <component
            :is="WidgetRegistry.get(w.type)?.component"
            v-if="WidgetRegistry.get(w.type)"
            :config="w.config"
            :datapoint-id="w.datapoint_id"
            :value="w.datapoint_id ? dpStore.getValue(w.datapoint_id) : null"
            :editor-mode="false"
          />
          <div v-else class="flex items-center justify-center h-full text-gray-600 text-xs">
            Unbekannter Widget-Typ: {{ w.type }}
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
