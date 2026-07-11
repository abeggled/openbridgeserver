<script setup lang="ts">
/**
 * WidgetRef — Widget-Referenz
 *
 * Lädt im Viewer eine andere Seite, sucht ein benanntes Widget und rendert
 * dessen Komponente mit Live-Datenpunkt-Werten. Ermöglicht es, ein einmal
 * konfiguriertes Widget auf beliebig vielen Seiten zu verwenden.
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { getJwt, getSessionToken, visu } from '@/api/client'
import { createWebSocketClient, useWebSocket } from '@/composables/useWebSocket'
import { useDatapointsStore } from '@/stores/datapoints'
import { WidgetRegistry } from '@/widgets/registry'
import type { DataPointValue, WidgetInstance } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const dpStore = useDatapointsStore()
const sourceWidget = ref<WidgetInstance | null>(null)
const loading = ref(false)
const errorMsg = ref('')
const sourceSessionNodeId = ref('')
const sourceAccess = ref('public')
const sourceValues = ref<Record<string, DataPointValue>>({})
const sourceWs = createWebSocketClient()
const defaultWs = useWebSocket()

const sourcePageId     = computed(() => (props.config.source_page_id     as string | undefined) ?? '')
const sourceWidgetName = computed(() => (props.config.source_widget_name as string | undefined) ?? '')
const sourceSessionToken = computed(() => sourceSessionNodeId.value ? getSessionToken(sourceSessionNodeId.value) : null)
const sourceReadContext = computed(() => ({
  pageId: sourcePageId.value,
  ...(sourceSessionToken.value ? { sessionToken: sourceSessionToken.value } : {}),
}))
const sourceWriteContext = computed(() => ({
  ...sourceReadContext.value,
  definingId: sourceSessionNodeId.value || sourcePageId.value,
}))

sourceWs.onMessage((msg) => {
  if (msg.id && msg.v !== undefined) {
    const id = msg.id as string
    sourceValues.value[id] = {
      id,
      v: msg.v,
      u: (msg.u as string | null) ?? null,
      t: (msg.t as string | undefined) ?? new Date().toISOString(),
      q: (msg.q as DataPointValue['q']) ?? 'good',
    }
    defaultWs.dispatch(msg)
  }
})

async function resolveSourceSessionContext(pageId: string): Promise<{ sessionNodeId: string; access: string }> {
  try {
    const breadcrumb = await visu.getBreadcrumb(pageId)
    const definingNode = [...breadcrumb].reverse().find(node => node.access !== null)
    return {
      sessionNodeId: definingNode?.id ?? pageId,
      access: definingNode?.access ?? 'public',
    }
  } catch {
    return { sessionNodeId: pageId, access: 'public' }
  }
}

async function loadReference() {
  sourceWs.disconnect()
  sourceValues.value = {}
  if (!sourcePageId.value || !sourceWidgetName.value) {
    sourceWidget.value = null
    sourceSessionNodeId.value = ''
    sourceAccess.value = 'public'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const sourceContext = await resolveSourceSessionContext(sourcePageId.value)
    sourceSessionNodeId.value = sourceContext.sessionNodeId
    sourceAccess.value = sourceContext.access
    const widgets = await visu.getWidgetRef(sourcePageId.value, sourceSessionNodeId.value)
    const found = widgets.find(w => w.name === sourceWidgetName.value) ?? null

    if (found) {
      const ids = [found.datapoint_id, found.status_datapoint_id].filter(Boolean) as string[]
      if (ids.length) {
        const preferPageScope = !getJwt() && sourceAccess.value !== 'user' && (sourceAccess.value !== 'protected' || !!sourceSessionToken.value)
        sourceWs.connect({
          ...sourceReadContext.value,
          ...(preferPageScope ? { preferPageScope } : {}),
        })
        sourceWs.subscribe(ids)
        dpStore.fetchInitialValues(ids, sourceReadContext.value)
      }
      sourceWidget.value = found
    } else {
      errorMsg.value = `Widget „${sourceWidgetName.value}" nicht gefunden`
      sourceWidget.value = null
    }
  } catch {
    errorMsg.value = 'Quell-Seite nicht erreichbar'
    sourceWidget.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => { if (!props.editorMode) loadReference() })
watch([sourcePageId, sourceWidgetName], () => { if (!props.editorMode) loadReference() })
onUnmounted(() => { sourceWs.disconnect() })

const refDef         = computed(() => sourceWidget.value ? WidgetRegistry.get(sourceWidget.value.type) : null)
const refValue = computed(() => {
  const id = sourceWidget.value?.datapoint_id
  return id ? (sourceValues.value[id] ?? dpStore.getValue(id)) : null
})
const refStatusValue = computed(() => {
  const id = sourceWidget.value?.status_datapoint_id
  return id ? (sourceValues.value[id] ?? dpStore.getValue(id)) : null
})
</script>

<template>
  <!-- Editor-Vorschau -->
  <div
    v-if="editorMode"
    class="flex flex-col items-center justify-center h-full gap-1.5 p-2 text-center"
  >
    <span class="text-2xl leading-none">🔗</span>
    <span v-if="sourceWidgetName" class="text-xs font-medium text-gray-600 dark:text-gray-300 truncate max-w-full">
      {{ sourceWidgetName }}
    </span>
    <span v-if="sourceWidgetName && sourcePageId" class="text-xs text-gray-400 dark:text-gray-600 truncate max-w-full">
      {{ $t('widgets.widgetref.reference') }}
    </span>
    <span v-else class="text-xs text-gray-300 dark:text-gray-700">{{ $t('widgets.widgetref.chooseReference') }}</span>
  </div>

  <!-- Viewer: Laden -->
  <div v-else-if="loading" class="flex items-center justify-center h-full text-gray-400 dark:text-gray-500 text-xs">
    …
  </div>

  <!-- Viewer: Fehler / nicht konfiguriert -->
  <div
    v-else-if="errorMsg || !sourceWidget || !refDef"
    class="flex items-center justify-center h-full text-xs p-2 text-center"
    :class="errorMsg ? 'text-red-400 dark:text-red-500' : 'text-gray-400 dark:text-gray-600'"
  >
    {{ errorMsg || '🔗 Keine Referenz' }}
  </div>

  <!-- Viewer: Referenziertes Widget rendern -->
  <component
    v-else
    :is="refDef!.component"
    :config="sourceWidget!.config"
    :datapoint-id="sourceWidget!.datapoint_id"
    :value="refValue"
    :status-value="refStatusValue"
    :editor-mode="false"
    :readonly="props.readonly"
    :page-id="sourcePageId"
    :session-token="sourceSessionToken"
    :write-context="sourceWriteContext"
  />
</template>
