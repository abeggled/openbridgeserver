<template>
  <div class="h-full flex flex-col bg-surface-800 border-r border-slate-200 dark:border-slate-700/60 w-56">
    <div class="px-3 py-2 border-b border-slate-200 dark:border-slate-700/60">
      <h3 class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{{ $t('logic.palette.title') }}</h3>
    </div>
    <div class="flex-1 overflow-y-auto p-2 flex flex-col gap-3">
      <div v-for="cat in categories" :key="cat.id">
        <div class="text-xs text-slate-400 dark:text-slate-500 uppercase tracking-wider px-1 mb-1">{{ cat.label }}</div>
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

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  nodeTypes: { type: Array, default: () => [] }
})
const emit = defineEmits(['drag-start'])

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

function onDragStart(event, nodeType) {
  event.dataTransfer.setData('application/vueflow-node-type', nodeType.type)
  event.dataTransfer.effectAllowed = 'move'
  emit('drag-start', nodeType)
}
</script>
