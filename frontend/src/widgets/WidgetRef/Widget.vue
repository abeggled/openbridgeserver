<script setup lang="ts">
/**
 * WidgetRef — Widget-Referenz
 *
 * Lädt im Viewer eine andere Seite, sucht ein benanntes Widget und rendert
 * dessen Komponente mit Live-Datenpunkt-Werten. Ermöglicht es, ein einmal
 * konfiguriertes Widget auf beliebig vielen Seiten zu verwenden.
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { visu } from '@/api/client'
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

const { t } = useI18n()
const dpStore = useDatapointsStore()
const sourceWidget = ref<WidgetInstance | null>(null)
const loading = ref(false)
const errorMsg = ref('')
let subscribedIds: string[] = []

const sourcePageId     = computed(() => (props.config.source_page_id     as string | undefined) ?? '')
const sourceWidgetName = computed(() => (props.config.source_widget_name as string | undefined) ?? '')

async function loadReference() {
  if (!sourcePageId.value || !sourceWidgetName.value) {
    sourceWidget.value = null
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const widgets = await visu.getWidgetRef(sourcePageId.value)
    const found = widgets.find(w => w.name === sourceWidgetName.value) ?? null

    // Alte Subscriptions ablösen
    if (subscribedIds.length) { dpStore.unsubscribe(subscribedIds); subscribedIds = [] }

    if (found) {
      const ids = [found.datapoint_id, found.status_datapoint_id].filter(Boolean) as string[]
      if (ids.length) {
        dpStore.subscribe(ids)
        dpStore.fetchInitialValues(ids)
        subscribedIds = ids
      }
      sourceWidget.value = found
    } else {
      errorMsg.value = t('widgets.widgetref.widgetNotFound', { name: sourceWidgetName.value })
      sourceWidget.value = null
    }
  } catch {
    errorMsg.value = t('widgets.widgetref.sourceUnavailable')
    sourceWidget.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => { if (!props.editorMode) loadReference() })
watch([sourcePageId, sourceWidgetName], () => { if (!props.editorMode) loadReference() })
onUnmounted(() => { if (subscribedIds.length) dpStore.unsubscribe(subscribedIds) })

const refDef         = computed(() => sourceWidget.value ? WidgetRegistry.get(sourceWidget.value.type) : null)
const refValue       = computed(() => sourceWidget.value?.datapoint_id        ? dpStore.getValue(sourceWidget.value.datapoint_id)        : null)
const refStatusValue = computed(() => sourceWidget.value?.status_datapoint_id ? dpStore.getValue(sourceWidget.value.status_datapoint_id) : null)
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
    <span v-else class="text-xs text-gray-300 dark:text-gray-700">{{ $t('widgets.widgetref.selectReference') }}</span>
  </div>

  <!-- Viewer: Laden -->
  <div v-else-if="loading" class="flex items-center justify-center h-full text-gray-400 dark:text-gray-500 text-xs">
    {{ $t('common.loading') }}
  </div>

  <!-- Viewer: Fehler / nicht konfiguriert -->
  <div
    v-else-if="errorMsg || !sourceWidget || !refDef"
    class="flex items-center justify-center h-full text-xs p-2 text-center"
    :class="errorMsg ? 'text-red-400 dark:text-red-500' : 'text-gray-400 dark:text-gray-600'"
  >
    <template v-if="errorMsg">{{ errorMsg }}</template>
    <template v-else>🔗 {{ $t('widgets.widgetref.noReference') }}</template>
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
  />
</template>
