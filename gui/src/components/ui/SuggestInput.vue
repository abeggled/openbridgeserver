<template>
  <div ref="container" class="relative">
    <input
      :value="modelValue"
      :class="['input w-full', inputClass]"
      :placeholder="placeholder"
      :required="required"
      autocomplete="off"
      :data-testid="testid"
      @input="onInput"
      @focus="onFocus"
      @blur="close"
      @keydown.down.prevent="move(1)"
      @keydown.up.prevent="move(-1)"
      @keydown.enter="onEnter"
      @keydown.escape.prevent="close"
    />
    <div
      v-if="open && (loading || suggestions.length || (searched && emptyText))"
      class="absolute z-50 mt-1 w-full bg-white dark:bg-surface-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl max-h-64 overflow-y-auto"
      :data-testid="`${testid}-dropdown`"
    >
      <div v-if="loading" class="px-3 py-2 text-xs text-slate-500">{{ $t('common.searching') }}</div>
      <div v-else-if="!suggestions.length" class="px-3 py-2 text-xs text-slate-500" :data-testid="`${testid}-empty`">{{ emptyText }}</div>
      <ul v-else>
        <li
          v-for="(item, i) in suggestions"
          :key="item.value"
          :class="['px-3 py-1.5 cursor-pointer text-sm', i === active ? 'bg-blue-600/20' : 'hover:bg-slate-100/80 dark:hover:bg-slate-700/50']"
          :data-testid="`${testid}-option-${i}`"
          @mousedown.prevent="select(item)"
          @mouseenter="active = i"
        >
          <div class="flex items-center gap-2 min-w-0">
            <span class="font-mono truncate">{{ item.value }}</span>
            <span v-if="item.label" class="text-xs text-slate-500 truncate">{{ item.label }}</span>
            <span v-if="item.detail" class="ml-auto text-xs text-slate-500 shrink-0">{{ item.detail }}</span>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
// Free-text input with a searchable suggestion list: the typed value is always
// kept (so it works offline), picking a suggestion just fills it in.
import { onMounted, onUnmounted, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  // async (query) => [{ value, label?, detail? }]
  fetchSuggestions: { type: Function, required: true },
  placeholder: { type: String, default: '' },
  required: { type: Boolean, default: false },
  testid: { type: String, default: 'suggest' },
  inputClass: { type: String, default: '' },
  debounceMs: { type: Number, default: 250 },
  emptyText: { type: String, default: '' },  // shown when a search finds nothing
})
const emit = defineEmits(['update:modelValue', 'select'])

const container = ref(null)
const open = ref(false)
const loading = ref(false)
const searched = ref(false)
const suggestions = ref([])
const active = ref(-1)
let timer = null
let seq = 0

async function fetchNow(query) {
  const mine = ++seq
  loading.value = true
  try {
    const items = await props.fetchSuggestions(query)
    if (mine !== seq) return
    suggestions.value = Array.isArray(items) ? items : []
  } catch {
    if (mine !== seq) return
    suggestions.value = []
  } finally {
    if (mine === seq) {
      loading.value = false
      searched.value = true
      active.value = -1
    }
  }
}

function onFocus() {
  open.value = true
  fetchNow(props.modelValue)
}

function onInput(event) {
  const value = event.target.value
  emit('update:modelValue', value)
  open.value = true
  clearTimeout(timer)
  timer = setTimeout(() => fetchNow(value), props.debounceMs)
}

function move(step) {
  if (!open.value) {
    onFocus()
    return
  }
  if (!suggestions.value.length) return
  active.value = Math.min(Math.max(active.value + step, 0), suggestions.value.length - 1)
}

function onEnter(event) {
  if (open.value && active.value >= 0) {
    event.preventDefault()
    select(suggestions.value[active.value])
  }
}

function select(item) {
  emit('update:modelValue', item.value)
  emit('select', item)
  close()
}

function close() {
  clearTimeout(timer)  // a search still waiting for its debounce is no longer wanted
  open.value = false
  active.value = -1
}

function onClickOutside(event) {
  if (container.value && !container.value.contains(event.target)) close()
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onUnmounted(() => {
  document.removeEventListener('mousedown', onClickOutside)
  clearTimeout(timer)
})
</script>
