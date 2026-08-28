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
              <span class="text-xs text-slate-700 dark:text-slate-200 flex-1 min-w-0 truncate">{{ $te('logic.nodeTypes.' + nt.type) ? $t('logic.nodeTypes.' + nt.type) : nt.label }}</span>
              <HelpButton v-if="NODE_HELP_IDS[nt.type]" :help-id="NODE_HELP_IDS[nt.type]" compact class="flex-shrink-0" />
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
import HelpButton from '@/components/ui/HelpButton.vue'

const props = defineProps({
  nodeTypes: { type: Array, default: () => [] },
  collapsed: { type: Boolean, default: false },
})
const emit = defineEmits(['drag-start', 'toggle'])

const { t } = useI18n()

const CATEGORY_IDS = ['logic', 'datapoint', 'math', 'string', 'timer', 'astro', 'notification', 'integration', 'script', 'ai']

// Per-block-type help — documented one category at a time (see
// help/de/logic/blocks-<category>.md); a type with no entry here simply
// gets no help button yet rather than one pointing at nonexistent content.
const NODE_HELP_IDS = {
  and: 'logic-block-and',
  or: 'logic-block-or',
  xor: 'logic-block-xor',
  not: 'logic-block-not',
  gate: 'logic-block-gate',
  memory: 'logic-block-memory',
  change_filter: 'logic-block-change-filter',
  compare: 'logic-block-compare',
  hysteresis: 'logic-block-hysteresis',
  merge: 'logic-block-merge',
  decision: 'logic-block-decision',
  value_mapping: 'logic-block-value-mapping',
  const_value: 'logic-block-const-value',
  datapoint_read: 'logic-block-datapoint-read',
  datapoint_write: 'logic-block-datapoint-write',
  math_formula: 'logic-block-math-formula',
  math_map: 'logic-block-math-map',
  clamp: 'logic-block-clamp',
  random_value: 'logic-block-random-value',
  statistics: 'logic-block-statistics',
  avg_multi: 'logic-block-avg-multi',
  min_max_tracker: 'logic-block-min-max-tracker',
  consumption_counter: 'logic-block-consumption-counter',
  heating_circuit: 'logic-block-heating-circuit',
  string_concat: 'logic-block-string-concat',
  string_replace: 'logic-block-string-replace',
  comment: 'logic-block-comment',
  timer_cron: 'logic-block-timer-cron',
  datetime: 'logic-block-datetime',
  timer_delay: 'logic-block-timer-delay',
  timer_pulse: 'logic-block-timer-pulse',
  operating_hours: 'logic-block-operating-hours',
  value_sequence: 'logic-block-value-sequence',
  astro_sun: 'logic-block-astro-sun',
  notify_message: 'logic-block-notify-message',
  message_archive: 'logic-block-message-archive',
  wake_on_lan: 'logic-block-wake-on-lan',
  host_check: 'logic-block-host-check',
  json_extractor: 'logic-block-json-extractor',
  xml_extractor: 'logic-block-xml-extractor',
  substring_extractor: 'logic-block-substring-extractor',
  ical: 'logic-block-ical',
  api_client: 'logic-block-api-client',
  python_script: 'logic-block-python-script',
  ai_logic: 'logic-block-ai-logic',
}

const categories = computed(() =>
  CATEGORY_IDS
    .map(id => ({
      id,
      label: t('logic.palette.categories.' + id),
      types: props.nodeTypes.filter(nt => nt.category === id && !nt.hidden_from_palette)
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
