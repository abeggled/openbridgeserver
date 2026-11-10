<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVisuStore } from '@/stores/visu'
import type { VisuNode } from '@/types'
import VisuIcon from '@/components/VisuIcon.vue'

// Suchbarer Picker für eine Seite oder einen Bereich der Visu; v-model ist die Knoten-ID.
const model = defineModel<string>({ default: '' })

const store = useVisuStore()

onMounted(async () => { if (!store.treeLoaded) await store.loadTree() })

// Vollständigen Pfad eines Knotens aufbauen
function nodePath(node: VisuNode): string {
  const parts: string[] = []
  let cur: VisuNode | undefined = node
  while (cur) {
    parts.unshift(cur.name)
    cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
  }
  return parts.join(' / ')
}

const searchQuery  = ref('')
const pickerOpen   = ref(false)
const searchInput  = ref<HTMLInputElement | null>(null)
const pickerEl     = ref<HTMLElement | null>(null)

const selectedNode = computed(() => (model.value ? store.getNode(model.value) : null))

const selectedNodeTitle = computed(() =>
  selectedNode.value ? nodePath(selectedNode.value) : undefined,
)

const filteredNodes = computed(() => {
  const q = searchQuery.value.toLowerCase().trim()
  return store.nodes
    .map(n => ({ node: n, path: nodePath(n) }))
    .filter(({ path }) => !q || path.toLowerCase().includes(q))
    .sort((a, b) => a.path.localeCompare(b.path))
    .slice(0, 50)
})

function openPicker() {
  pickerOpen.value  = true
  searchQuery.value = ''
  setTimeout(() => searchInput.value?.focus(), 30)
}

function selectNode(id: string) {
  model.value       = id
  pickerOpen.value  = false
  searchQuery.value = ''
}

function clearNode() {
  model.value = ''
}

// Click-Outside schliesst Picker
function onDocClick(e: MouseEvent) {
  if (pickerEl.value && !pickerEl.value.contains(e.target as Node)) {
    pickerOpen.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
  <div class="relative" ref="pickerEl">

    <!-- Anzeige: aktuell gewählte Seite -->
    <div
      v-if="!pickerOpen"
      class="flex items-center gap-2 w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 cursor-pointer hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
      data-testid="page-picker-display"
      @click="openPicker"
    >
      <span v-if="selectedNode" class="text-base leading-none flex-shrink-0">
        <VisuIcon :icon="selectedNode.icon ?? (selectedNode.type === 'PAGE' ? '📄' : '📁')" />
      </span>
      <span
        class="flex-1 text-sm truncate"
        :class="selectedNode ? 'text-gray-900 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'"
        :title="selectedNodeTitle"
      >
        {{ selectedNode ? nodePath(selectedNode) : $t('widgets.common.selectPage') }}
      </span>
      <button
        v-if="selectedNode"
        class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs flex-shrink-0"
        data-testid="page-picker-clear"
        @click.stop="clearNode"
      >✕</button>
      <span class="text-gray-400 text-xs flex-shrink-0">▾</span>
    </div>

    <!-- Suchfeld + Ergebnisse -->
    <div v-else class="border border-blue-500 rounded bg-white dark:bg-gray-800 overflow-hidden">
      <input
        ref="searchInput"
        v-model="searchQuery"
        type="text"
        :placeholder="$t('widgets.common.searchPage')"
        class="w-full bg-transparent px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none"
        @keydown.escape="pickerOpen = false"
      />
      <div class="max-h-52 overflow-y-auto border-t border-gray-200 dark:border-gray-700">
        <div v-if="filteredNodes.length === 0" class="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">
          {{ $t('widgets.common.noResults') }}
        </div>
        <button
          v-for="{ node, path } in filteredNodes"
          :key="node.id"
          type="button"
          class="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-left"
          :class="node.id === model ? 'bg-blue-50 dark:bg-blue-500/10' : ''"
          :title="path"
          :data-testid="`page-picker-option-${node.id}`"
          @click="selectNode(node.id)"
        >
          <span class="text-base leading-none flex-shrink-0">
            <VisuIcon :icon="node.icon ?? (node.type === 'PAGE' ? '📄' : '📁')" />
          </span>
          <span class="flex-1 min-w-0">
            <span class="block text-sm text-gray-900 dark:text-gray-100 truncate">{{ node.name }}</span>
            <span class="block text-xs text-gray-400 dark:text-gray-500 truncate">{{ path }}</span>
          </span>
          <span
            class="flex-shrink-0 text-xs px-1.5 py-0.5 rounded"
            :class="node.type === 'PAGE' ? 'text-blue-500 dark:text-blue-400' : 'text-purple-500 dark:text-purple-400'"
          >{{ node.type === 'PAGE' ? $t('widgets.common.nodeTypePage') : $t('widgets.common.nodeTypeArea') }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
