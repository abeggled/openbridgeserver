<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { visu } from '@/api/client'
import type { VisuNode } from '@/types'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', v: Record<string, unknown>): void }>()

const allNodes         = ref<VisuNode[]>([])
const pageWidgetNames  = ref<string[]>([])
const loadingWidgets   = ref(false)

const sourcePageId     = ref((props.modelValue.source_page_id     as string) ?? '')
const sourceWidgetName = ref((props.modelValue.source_widget_name as string) ?? '')

// Sync bei Widget-Wechsel im Editor
watch(() => props.modelValue, (v) => {
  sourcePageId.value     = (v.source_page_id     as string) ?? ''
  sourceWidgetName.value = (v.source_widget_name as string) ?? ''
})

// Gesamten Baum einmalig laden
onMounted(async () => {
  allNodes.value = await visu.tree()
  // Falls bereits eine Seite gesetzt ist, deren Widgets sofort laden
  if (sourcePageId.value) loadWidgetNames(sourcePageId.value)
})

// Nur PAGE-Knoten
const pages = computed(() => allNodes.value.filter(n => n.type === 'PAGE'))

// Vollständigen Pfad aufbauen: "Haus / Etage / Zimmer / Seite"
function pagePath(node: VisuNode): string {
  const map = Object.fromEntries(allNodes.value.map(n => [n.id, n]))
  const parts: string[] = []
  let cur: VisuNode | undefined = node
  while (cur) {
    parts.unshift(cur.name)
    cur = cur.parent_id ? map[cur.parent_id] : undefined
  }
  return parts.join(' / ')
}

// Benannte Widgets der gewählten Seite laden
async function loadWidgetNames(pageId: string) {
  if (!pageId) { pageWidgetNames.value = []; return }
  loadingWidgets.value = true
  try {
    const pc = await visu.getPage(pageId)
    pageWidgetNames.value = pc.widgets.map(w => w.name).filter(Boolean) as string[]
  } finally {
    loadingWidgets.value = false
  }
}

// Bei Seitenwechsel Widgets neu laden und Widget-Auswahl zurücksetzen
watch(sourcePageId, (id) => {
  sourceWidgetName.value = ''
  loadWidgetNames(id)
})

// Änderungen an Parent melden
watch([sourcePageId, sourceWidgetName], () => {
  emit('update:modelValue', {
    source_page_id:     sourcePageId.value     || null,
    source_widget_name: sourceWidgetName.value || null,
  })
})
</script>

<template>
  <div class="space-y-3">
    <!-- Quell-Seite -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.widgetref.sourcePage') }}</label>
      <select
        v-model="sourcePageId"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="">{{ $t('widgets.widgetref.selectPage') }}</option>
        <option v-for="p in pages" :key="p.id" :value="p.id">
          {{ pagePath(p) }}
        </option>
      </select>
    </div>

    <!-- Widget-Name -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.widgetref.widget') }}</label>
      <select
        v-model="sourceWidgetName"
        :disabled="!sourcePageId || loadingWidgets"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <option value="">
          {{ loadingWidgets ? $t('common.loading') : pageWidgetNames.length ? $t('widgets.widgetref.selectWidget') : $t('widgets.widgetref.noNamedWidgets') }}
        </option>
        <option v-for="name in pageWidgetNames" :key="name" :value="name">
          {{ name }}
        </option>
      </select>

      <!-- Hinweis wenn keine benannten Widgets vorhanden -->
      <p
        v-if="sourcePageId && !loadingWidgets && pageWidgetNames.length === 0"
        class="text-xs text-amber-500 dark:text-amber-400 mt-1.5"
      >
        {{ $t('widgets.widgetref.noNamedWidgetsHint') }}
      </p>
    </div>

    <!-- Vorschau der Referenz -->
    <div v-if="sourceWidgetName" class="rounded-lg bg-blue-500/10 dark:bg-blue-400/10 border border-blue-500/20 dark:border-blue-400/20 px-3 py-2">
      <p class="text-xs text-blue-600 dark:text-blue-400 font-medium">🔗 Referenz aktiv</p>
      <p class="text-xs text-blue-500 dark:text-blue-500 mt-0.5 truncate">{{ sourceWidgetName }}</p>
    </div>
  </div>
</template>
