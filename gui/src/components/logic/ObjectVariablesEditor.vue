<script setup>
// Object-variable slots (###OBS1###, ###OBS2###, …) of a logic block: shared by
// the API Client and the JSON/XML extractors (issue #1301).
import { computed, ref, watch } from 'vue'
import { dpApi, searchApi } from '@/api/client'
import { normaliseObjectVariables as normalise } from '@/utils/logicVariables'

const props = defineProps({
  modelValue: { type: [Array, String], default: () => [] },
  testIdPrefix: { type: String, default: 'variable' },
  hint: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const variableList = computed(() => normalise(props.modelValue))
const searches = ref([])
const results = ref([])
const knownIds = []
// Monotonic request counter per row: a slower, older response must not overwrite a newer one.
const requestSeq = []
let seqCounter = 0

watch(
  () => props.modelValue,
  (raw) => {
    const vars = normalise(raw)
    // A row whose object changed from outside (another node selected) drops its picker state.
    const changed = vars.map((v, i) => knownIds[i] !== v.datapoint_id)
    changed.forEach((isChanged, i) => { if (isChanged) requestSeq[i] = ++seqCounter })
    searches.value = vars.map((v, i) => (changed[i] ? v.datapoint_name ?? '' : searches.value[i] ?? v.datapoint_name ?? ''))
    results.value = vars.map((_, i) => (changed[i] ? [] : results.value[i] ?? []))
    knownIds.splice(0, knownIds.length, ...vars.map(v => v.datapoint_id))
  },
  { immediate: true, deep: true },
)

function commit(variables) {
  emit('update:modelValue', variables)
}

function add() {
  const variables = normalise(props.modelValue)
  const maxSlot = variables.reduce((max, variable) => Math.max(max, variable.slot || 0), 0)
  variables.push({ slot: maxSlot + 1, datapoint_id: '', datapoint_name: '' })
  commit(variables)
}

function remove(index) {
  const variables = normalise(props.modelValue)
  variables.splice(index, 1)
  searches.value.splice(index, 1)
  results.value.splice(index, 1)
  knownIds.splice(index, 1)
  // Rows shift down: every pending response for this index or later now targets the wrong row.
  const rowCount = requestSeq.length
  for (let i = index; i < rowCount; i++) requestSeq[i] = ++seqCounter
  commit(variables)
}

async function search(index, query) {
  const seq = (requestSeq[index] = ++seqCounter)
  let items
  try {
    const { data } = (query || '').length < 1
      ? await dpApi.list(0, 50)
      : await searchApi.search({ q: query, size: 50 })
    items = data.items || data
  } catch {
    items = []
  }
  if (seq !== requestSeq[index]) return
  const next = results.value.slice()
  next[index] = items
  results.value = next
}

function onSearchInput(index, event) {
  const query = event.target.value
  const next = searches.value.slice()
  next[index] = query
  searches.value = next
  search(index, query)
}

function select(index, dp) {
  requestSeq[index] = ++seqCounter
  const variables = normalise(props.modelValue)
  variables[index] = { slot: variables[index]?.slot || index + 1, datapoint_id: dp.id, datapoint_name: dp.name }
  const nextSearches = searches.value.slice()
  nextSearches[index] = dp.name
  searches.value = nextSearches
  const nextResults = results.value.slice()
  nextResults[index] = []
  results.value = nextResults
  commit(variables)
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="section-label flex items-center justify-between mt-1">
      <span>{{ $t('logic.nodeConfig.apiClient.variablesSection') }}</span>
      <button type="button" class="btn-secondary btn-sm text-teal-400" :data-testid="`${testIdPrefix}-add-variable`" @click="add">
        {{ $t('logic.nodeConfig.apiClient.addVariable') }}
      </button>
    </div>
    <p v-if="hint" class="text-xs text-slate-500 -mt-2">{{ hint }}</p>
    <div v-if="variableList.length === 0" class="text-xs text-slate-500 italic">
      {{ $t('logic.nodeConfig.apiClient.noVariables') }}
    </div>
    <div
      v-for="(variable, i) in variableList"
      :key="variable.slot || i"
      class="border border-slate-700 rounded-lg p-3 flex flex-col gap-2 bg-slate-900/40"
      :data-testid="`${testIdPrefix}-variable-${i}`"
    >
      <div class="flex items-center justify-between gap-2">
        <div class="min-w-0">
          <span class="text-xs font-semibold text-teal-400">OBS{{ variable.slot || i + 1 }}</span>
          <code class="ml-2 text-xs text-slate-400 break-all">###OBS{{ variable.slot || i + 1 }}###</code>
        </div>
        <button
          type="button"
          class="text-xs text-red-400 hover:text-red-300 shrink-0"
          :data-testid="`${testIdPrefix}-variable-remove-${i}`"
          @click="remove(i)"
        >
          {{ $t('logic.nodeConfig.apiClient.removeVariable') }}
        </button>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('logic.ports.object') }}</label>
        <input
          :value="searches[i] ?? variable.datapoint_name ?? ''"
          type="text"
          class="input text-sm"
          :placeholder="$t('logic.nodeConfig.connection.searchPlaceholder')"
          :data-testid="`${testIdPrefix}-variable-search-${i}`"
          @input="onSearchInput(i, $event)"
        />
        <div
          v-if="results[i]?.length"
          class="mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden max-h-40 overflow-y-auto"
        >
          <button
            v-for="dp in results[i]"
            :key="dp.id"
            type="button"
            class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200"
            :data-testid="`${testIdPrefix}-variable-result-${i}`"
            @click="select(i, dp)"
          >
            {{ dp.name }}
            <span class="text-slate-500 ml-1">{{ dp.data_type }}</span>
          </button>
        </div>
        <div v-if="variable.datapoint_name" class="mt-1 text-xs text-teal-400">
          ✓ {{ variable.datapoint_name }}
        </div>
      </div>
    </div>
  </div>
</template>
