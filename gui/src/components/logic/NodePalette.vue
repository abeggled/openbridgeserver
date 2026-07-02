<template>
  <div :class="['h-full flex flex-col bg-surface-800 border-r border-slate-200 dark:border-slate-700/60 transition-all duration-300 flex-shrink-0', collapsed ? 'w-8' : 'w-56']">

    <!-- Collapsed: single expand button -->
    <template v-if="collapsed">
      <button
        @click="$emit('toggle')"
        class="h-full w-full flex items-center justify-center hover:bg-slate-700/40 transition-colors"
        :title="$t('logic.palette.expand')"
      >
        <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
        </svg>
      </button>
    </template>

    <!-- Expanded -->
    <template v-else>
      <!-- Header -->
      <button
        @click="$emit('toggle')"
        class="px-3 py-2 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between flex-shrink-0 w-full hover:bg-slate-700/40 transition-colors"
        :title="$t('logic.palette.collapse')"
      >
        <h3 class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{{ $t('logic.palette.title') }}</h3>
        <svg class="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
      </button>

      <!-- Categories -->
      <div class="flex-1 overflow-y-auto p-2 flex flex-col gap-1">
        <div v-for="cat in categories" :key="cat.id">
          <!-- Section header -->
          <button
            @click="toggleCategory(cat.id)"
            class="w-full flex items-center justify-between px-1 py-1 text-xs text-slate-400 dark:text-slate-500 uppercase tracking-wider hover:bg-slate-700/40 hover:text-slate-200 dark:hover:text-slate-300 transition-colors rounded"
          >
            <span>{{ cat.label }}</span>
            <svg
              :class="['w-3 h-3 transition-transform duration-200', collapsedCategories.has(cat.id) ? '-rotate-90' : '']"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>

          <!-- Block list -->
          <div v-show="!collapsedCategories.has(cat.id)" class="flex flex-col gap-0.5 mb-1">
            <div
              v-for="nt in cat.types" :key="nt.type"
              draggable="true"
              @dragstart="onDragStart($event, nt)"
              class="flex items-center gap-2 px-2 py-1.5 rounded cursor-grab hover:bg-slate-100 dark:hover:bg-slate-700/50 transition-colors select-none"
            >
              <span class="w-2 h-2 rounded-full flex-shrink-0" :style="{ background: nt.color }"></span>
              <span class="text-xs text-slate-700 dark:text-slate-200">{{ $te('logic.nodeTypes.' + nt.type) ? $t('logic.nodeTypes.' + nt.type) : nt.label }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>

  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  nodeTypes: { type: Array, default: () => [] },
  collapsed: { type: Boolean, default: false },
})
const emit = defineEmits(['drag-start', 'toggle'])

const { t } = useI18n()

const CATEGORY_IDS = ['logic', 'datapoint', 'math', 'string', 'timer', 'astro', 'notification', 'integration', 'script', 'ai']

const categories = computed(() =>
  CATEGORY_IDS
    .map(id => ({
      id,
      label: t('logic.palette.categories.' + id),
      types: props.nodeTypes.filter(nt => nt.category === id)
    }))
    .filter(cat => cat.types.length > 0)
)

const CATS_KEY = 'logic_palette_collapsed_cats'
let _savedCats = []
try {
  const _parsed = JSON.parse(localStorage.getItem(CATS_KEY) ?? '[]')
  if (Array.isArray(_parsed)) _savedCats = _parsed
} catch { /* ignore malformed storage */ }
const collapsedCategories = ref(new Set(_savedCats))

function toggleCategory(id) {
  const next = new Set(collapsedCategories.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsedCategories.value = next
  localStorage.setItem(CATS_KEY, JSON.stringify([...next]))
}

function onDragStart(event, nodeType) {
  event.dataTransfer.setData('application/vueflow-node-type', nodeType.type)
  event.dataTransfer.effectAllowed = 'move'
  emit('drag-start', nodeType)
}
</script>
