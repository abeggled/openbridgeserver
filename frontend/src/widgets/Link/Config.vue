<script setup lang="ts">
import { reactive, ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useVisuStore } from '@/stores/visu'
import type { VisuNode } from '@/types'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const store = useVisuStore()

const cfg = reactive({
  label:          (props.modelValue.label          as string) ?? '',
  icon:           (props.modelValue.icon           as string) ?? '🔗',
  target_node_id: (props.modelValue.target_node_id as string) ?? '',
})

// Sync bei Widget-Wechsel
watch(() => props.modelValue, (v) => {
  cfg.label          = (v.label          as string) ?? ''
  cfg.icon           = (v.icon           as string) ?? '🔗'
  cfg.target_node_id = (v.target_node_id as string) ?? ''
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

// Baum laden
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

// ── Seiten-Picker ─────────────────────────────────────────────────────────────
const searchQuery  = ref('')
const pickerOpen   = ref(false)
const searchInput  = ref<HTMLInputElement | null>(null)
const pickerEl     = ref<HTMLElement | null>(null)

const selectedNode = computed(() =>
  cfg.target_node_id ? store.getNode(cfg.target_node_id) : null,
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
  cfg.target_node_id = id
  pickerOpen.value   = false
  searchQuery.value  = ''
}

function clearNode() {
  cfg.target_node_id = ''
}

// Click-Outside schliesst Picker
function onDocClick(e: MouseEvent) {
  if (pickerEl.value && !pickerEl.value.contains(e.target as Node)) {
    pickerOpen.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))

// ── Icon-Picker ───────────────────────────────────────────────────────────────
const QUICK_ICONS = [
  '🔗','🏠','🏡','📁','📄','⚡','💡','🌡️','🔒','🚿','🛁','🛋️',
  '🍳','🛏️','🪟','🚪','🌿','🔆','🔅','💧','🌬️','🎵','📹','🔔',
  '🌞','🌙','🎛️','🎚️','🔌','🌐','⚙️','🏊','🌳','🚗','🔑','📊',
]
</script>

<template>
  <div class="space-y-4">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wohnzimmer"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Icon-Auswahl -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Icon</label>
      <div class="flex items-start gap-2">
        <!-- Textfeld für freie Eingabe -->
        <input
          v-model="cfg.icon"
          type="text"
          maxlength="4"
          placeholder="🔗"
          class="w-14 text-center bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-lg focus:outline-none focus:border-blue-500"
        />
        <!-- Schnellauswahl -->
        <div class="flex flex-wrap gap-1">
          <button
            v-for="ic in QUICK_ICONS"
            :key="ic"
            type="button"
            class="w-7 h-7 text-base flex items-center justify-center rounded transition-colors hover:bg-gray-100 dark:hover:bg-gray-700"
            :class="cfg.icon === ic ? 'ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-500/20' : ''"
            :title="ic"
            @click="cfg.icon = ic"
          >{{ ic }}</button>
        </div>
      </div>
    </div>

    <!-- Ziel-Seite (suchbarer Picker) -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Ziel-Seite</label>
      <div class="relative" ref="pickerEl">

        <!-- Anzeige: aktuell gewählte Seite -->
        <div
          v-if="!pickerOpen"
          class="flex items-center gap-2 w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 cursor-pointer hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
          @click="openPicker"
        >
          <span v-if="selectedNode" class="text-base leading-none flex-shrink-0">
            {{ selectedNode.icon ?? (selectedNode.type === 'PAGE' ? '📄' : '📁') }}
          </span>
          <span
            class="flex-1 text-sm truncate"
            :class="selectedNode ? 'text-gray-900 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'"
            :title="selectedNode ? nodePath(selectedNode) : undefined"
          >
            {{ selectedNode ? nodePath(selectedNode) : 'Seite wählen …' }}
          </span>
          <button
            v-if="selectedNode"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs flex-shrink-0"
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
            placeholder="Seitenname suchen …"
            class="w-full bg-transparent px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none"
            @keydown.escape="pickerOpen = false"
          />
          <div class="max-h-52 overflow-y-auto border-t border-gray-200 dark:border-gray-700">
            <div v-if="filteredNodes.length === 0" class="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">
              Keine Treffer
            </div>
            <button
              v-for="{ node, path } in filteredNodes"
              :key="node.id"
              type="button"
              class="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-left"
              :class="node.id === cfg.target_node_id ? 'bg-blue-50 dark:bg-blue-500/10' : ''"
              :title="path"
              @click="selectNode(node.id)"
            >
              <span class="text-base leading-none flex-shrink-0">
                {{ node.icon ?? (node.type === 'PAGE' ? '📄' : '📁') }}
              </span>
              <span class="flex-1 min-w-0">
                <span class="block text-sm text-gray-900 dark:text-gray-100 truncate">{{ node.name }}</span>
                <span class="block text-xs text-gray-400 dark:text-gray-500 truncate">{{ path }}</span>
              </span>
              <span
                class="flex-shrink-0 text-xs px-1.5 py-0.5 rounded"
                :class="node.type === 'PAGE' ? 'text-blue-500 dark:text-blue-400' : 'text-purple-500 dark:text-purple-400'"
              >{{ node.type === 'PAGE' ? 'Seite' : 'Bereich' }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>
