<template>
  <div class="relative" ref="container">
    <input
      v-model="query"
      @focus="onFocus"
      @keydown.down.prevent="moveDown"
      @keydown.up.prevent="moveUp"
      @keydown.enter.prevent="selectActive"
      @keydown.escape="close"
      :placeholder="placeholder"
      class="input pr-8"
      autocomplete="off"
    />
    <!-- Clear button -->
    <button v-if="query" type="button" @click="clear"
      class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
      </svg>
    </button>

    <!-- Dropdown -->
    <div v-if="open && (suggestions.length || loading || noResults)"
      class="absolute z-50 mt-1 w-full bg-white dark:bg-surface-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl max-h-64 overflow-y-auto">

      <!-- Loading -->
      <div v-if="loading" class="px-3 py-2 text-xs text-slate-500 flex items-center gap-2">
        <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
        </svg>
        Suche …
      </div>

      <!-- No results -->
      <div v-else-if="noResults" class="px-3 py-2 text-xs text-slate-500">Keine Objekte gefunden</div>

      <!-- Suggestions -->
      <ul v-else>
        <li v-for="(item, i) in suggestions" :key="item.id"
          @click="select(item)"
          @mouseenter="activeIndex = i"
          :class="['px-3 py-2 cursor-pointer flex items-center gap-2 text-sm transition-colors',
            i === activeIndex
              ? 'bg-blue-600/20 text-slate-800 dark:text-slate-100'
              : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100/80 dark:hover:bg-slate-700/50']">
          <span class="flex-1 min-w-0 truncate">{{ item.name }}</span>
          <span class="text-xs text-slate-500 shrink-0">{{ item.data_type }}</span>
          <span v-if="item.unit" class="text-xs text-slate-600 shrink-0">{{ item.unit }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { searchApi } from '@/api/client'

const props = defineProps({
  modelValue: { type: String, default: '' },   // selected DP id
  displayName: { type: String, default: '' },  // shown in the input when an item is selected
  placeholder: { type: String, default: 'Name, UUID, Konfiguration …' },
})
const emit = defineEmits(['update:modelValue', 'select'])

const query       = ref(props.displayName || '')
const suggestions = ref([])
const loading     = ref(false)
const noResults   = ref(false)
const open        = ref(false)
const activeIndex = ref(-1)
const container   = ref(null)

let debounceTimer = null
let _skipSearch = false  // suppresses search when displayName syncs programmatically

// When the parent updates displayName (e.g. on mount after async load), sync input
watch(() => props.displayName, val => {
  if (val && !open.value) {
    _skipSearch = true
    query.value = val
  }
})

// Trigger search on every keystroke
watch(query, val => {
  if (_skipSearch) { _skipSearch = false; return }
  clearTimeout(debounceTimer)
  noResults.value = false

  if (!val) {
    suggestions.value = []
    // Don't emit empty — only emit on explicit clear()
    return
  }
  loading.value = true
  open.value = true
  debounceTimer = setTimeout(() => doSearch(val), 200)
})

async function doSearch(q) {
  try {
    const { data } = await searchApi.search({ q, size: 50 })
    suggestions.value = data.items || data
    noResults.value   = suggestions.value.length === 0
    activeIndex.value = -1
  } catch {
    suggestions.value = []
    noResults.value = true
  } finally {
    loading.value = false
  }
}

async function onFocus() {
  // Show first page on focus if input is empty
  if (!query.value) {
    try {
      loading.value = true
      open.value = true
      const { data } = await searchApi.search({ q: '', size: 50 })
      suggestions.value = data.items || data
      noResults.value   = suggestions.value.length === 0
    } catch {
      suggestions.value = []
    } finally {
      loading.value = false
    }
  } else {
    open.value = true
    if (!suggestions.value.length) doSearch(query.value)
  }
}

function select(item) {
  query.value = item.name
  emit('update:modelValue', item.id)
  emit('select', item)
  close()
}

function clear() {
  query.value = ''
  suggestions.value = []
  noResults.value = false
  open.value = false
  emit('update:modelValue', '')
  emit('select', null)
}

function close() {
  open.value = false
  activeIndex.value = -1
}

function moveDown() {
  if (!open.value) return
  activeIndex.value = Math.min(activeIndex.value + 1, suggestions.value.length - 1)
}

function moveUp() {
  activeIndex.value = Math.max(activeIndex.value - 1, 0)
}

function selectActive() {
  if (activeIndex.value >= 0 && suggestions.value[activeIndex.value]) {
    select(suggestions.value[activeIndex.value])
  }
}

function onClickOutside(e) {
  if (container.value && !container.value.contains(e.target)) close()
}
onMounted(() => document.addEventListener('mousedown', onClickOutside))
onUnmounted(() => document.removeEventListener('mousedown', onClickOutside))
</script>
