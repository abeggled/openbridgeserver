<template>
  <div class="relative" ref="container" data-testid="combobox-root">
    <!-- Wrapper that hosts the chips inline with the input field in multi mode -->
    <div
      :class="[
        'flex flex-wrap items-center gap-1 input pr-8',
        multi ? 'min-h-9 py-1' : '',
        wrapperClass,
      ]"
      @click="focusInput"
    >
      <!-- Chips (multi mode) -->
      <template v-if="multi">
        <span
          v-for="(item, i) in selectedItems"
          :key="item.id"
          :data-testid="`combobox-chip-${i}`"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-blue-500/15 text-blue-700 dark:text-blue-300 border border-blue-500/30 max-w-full min-w-0"
        >
          <slot name="chip" :item="item" :index="i" :remove="() => removeAt(i)">
            <span class="truncate">{{ chipLabel(item) }}</span>
          </slot>
          <button
            type="button"
            :data-testid="`combobox-chip-remove-${i}`"
            class="ml-0.5 text-blue-700/70 hover:text-red-500 dark:text-blue-300/70 dark:hover:text-red-400 shrink-0"
            @click.stop="removeAt(i)"
            tabindex="-1"
            aria-label="Entfernen"
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </span>
      </template>

      <input
        ref="inputRef"
        v-model="query"
        @focus="onFocus"
        @input="onInput"
        @keydown.down.prevent="moveDown"
        @keydown.up.prevent="moveUp"
        @keydown.enter.prevent="selectActive"
        @keydown.escape.prevent="close"
        @keydown.backspace="onBackspace"
        :placeholder="effectivePlaceholder"
        class="flex-1 min-w-[6rem] bg-transparent border-0 outline-none focus:ring-0 p-0 text-sm"
        autocomplete="off"
        data-testid="combobox-input"
      />
    </div>

    <!-- Clear button (single mode only) -->
    <button
      v-if="!multi && query"
      type="button"
      data-testid="combobox-clear"
      @click="clearQuery"
      class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
      tabindex="-1"
    >
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
      </svg>
    </button>

    <!-- Dropdown -->
    <div
      v-if="open && (suggestions.length || loading || noResults)"
      data-testid="combobox-dropdown"
      class="absolute z-50 mt-1 w-full bg-white dark:bg-surface-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl max-h-64 overflow-y-auto"
    >
      <div v-if="loading" data-testid="combobox-loading" class="px-3 py-2 text-xs text-slate-500">Suche …</div>
      <div v-else-if="noResults" data-testid="combobox-empty" class="px-3 py-2 text-xs text-slate-500">
        {{ emptyText }}
      </div>
      <ul v-else>
        <li
          v-for="(item, i) in suggestions"
          :key="item.id"
          :data-testid="`combobox-item-${i}`"
          @mousedown.prevent="select(item)"
          @click="select(item)"
          @mouseenter="activeIndex = i"
          :class="[
            'px-3 py-2 cursor-pointer flex items-center gap-2 text-sm transition-colors',
            i === activeIndex
              ? 'bg-blue-600/20 text-slate-800 dark:text-slate-100 active'
              : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100/80 dark:hover:bg-slate-700/50',
            isSelected(item) ? 'opacity-60' : '',
          ]"
        >
          <slot name="item" :item="item" :index="i" :active="i === activeIndex" :selected="isSelected(item)">
            <span class="flex-1 min-w-0 truncate">{{ chipLabel(item) }}</span>
          </slot>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Array], default: '' },
  multi: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  fetchSuggestions: { type: Function, required: true },
  displayItems: { type: Array, default: () => [] }, // chip labels for multi mode
  emptyText: { type: String, default: 'Keine Treffer' },
  debounceMs: { type: Number, default: 200 },
  wrapperClass: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'select'])

const container = ref(null)
const inputRef = ref(null)
// Initial input text: in single mode, derive from displayItems if available.
function initialQuery() {
  if (props.multi) return ''
  if (!props.modelValue) return ''
  const match = (props.displayItems || []).find((it) => it.id === props.modelValue)
  return match ? chipLabelStatic(match) : ''
}
function chipLabelStatic(item) {
  return item?.label ?? item?.name ?? String(item?.id ?? '')
}
const query = ref(initialQuery())
const suggestions = ref([])
const loading = ref(false)
const noResults = ref(false)
const open = ref(false)
const activeIndex = ref(-1)
let debounceTimer = null

const selectedIds = computed(() => {
  if (props.multi) return Array.isArray(props.modelValue) ? props.modelValue : []
  return props.modelValue ? [props.modelValue] : []
})

const selectedItems = computed(() => {
  if (!props.multi) return []
  const byId = new Map((props.displayItems || []).map((it) => [it.id, it]))
  return selectedIds.value.map((id) => byId.get(id) || { id, label: String(id) })
})

const effectivePlaceholder = computed(() => {
  if (props.multi && selectedIds.value.length) return ''
  return props.placeholder
})

function chipLabel(item) {
  return item?.label ?? item?.name ?? String(item?.id ?? '')
}

function focusInput() {
  inputRef.value?.focus()
}

function isSelected(item) {
  return selectedIds.value.includes(item.id)
}

async function runFetch(q) {
  loading.value = true
  open.value = true
  try {
    const data = await props.fetchSuggestions(q)
    suggestions.value = Array.isArray(data) ? data : []
    noResults.value = suggestions.value.length === 0
    activeIndex.value = -1
  } catch {
    suggestions.value = []
    noResults.value = true
    activeIndex.value = -1
  } finally {
    loading.value = false
  }
}

function debouncedFetch(q) {
  clearTimeout(debounceTimer)
  if (props.debounceMs <= 0) {
    runFetch(q)
    return
  }
  debounceTimer = setTimeout(() => runFetch(q), props.debounceMs)
}

function onFocus() {
  open.value = true
  runFetch(query.value)
}

function onInput() {
  open.value = true
  debouncedFetch(query.value)
}

function moveDown() {
  if (!open.value) {
    open.value = true
    runFetch(query.value)
    return
  }
  if (!suggestions.value.length) return
  activeIndex.value = Math.min(activeIndex.value + 1, suggestions.value.length - 1)
}

function moveUp() {
  if (!suggestions.value.length) return
  activeIndex.value = Math.max(activeIndex.value - 1, 0)
}

function selectActive() {
  if (activeIndex.value >= 0 && suggestions.value[activeIndex.value]) {
    select(suggestions.value[activeIndex.value])
  }
}

function select(item) {
  if (props.multi) {
    if (isSelected(item)) {
      // already there — do not re-add
      query.value = ''
      return
    }
    const next = [...selectedIds.value, item.id]
    emit('update:modelValue', next)
    emit('select', item)
    query.value = ''
    // Close the dropdown after each pick — leaving it open caused it to
    // visually overlap the next field below (e.g. DataPoints under
    // Hierarchy-Knoten) and stray clicks landed on the wrong combobox.
    // User re-opens by focusing the input again, which feels natural in a
    // form with multiple comboboxes stacked vertically.
    close()
    return
  }
  emit('update:modelValue', item.id)
  emit('select', item)
  query.value = chipLabel(item)
  close()
}

function removeAt(idx) {
  if (!props.multi) return
  const next = selectedIds.value.slice()
  next.splice(idx, 1)
  emit('update:modelValue', next)
}

function onBackspace() {
  if (!props.multi) return
  if (query.value) return
  if (!selectedIds.value.length) return
  removeAt(selectedIds.value.length - 1)
}

function clearQuery() {
  query.value = ''
  if (!props.multi) {
    emit('update:modelValue', '')
    emit('select', null)
  }
  close()
}

function close() {
  open.value = false
  activeIndex.value = -1
}

function onClickOutside(e) {
  if (container.value && !container.value.contains(e.target)) close()
}

onMounted(() => {
  document.addEventListener('mousedown', onClickOutside)
})
onUnmounted(() => {
  document.removeEventListener('mousedown', onClickOutside)
  clearTimeout(debounceTimer)
})

// Allow parent to reset query when modelValue is cleared externally (single mode).
watch(
  () => props.modelValue,
  (val) => {
    if (props.multi) return
    if (!val) {
      // only reset if not currently being edited
      if (!open.value) query.value = ''
      return
    }
    // If we know a label for the current id and the input is not being edited,
    // reflect it.
    if (!open.value) {
      const match = (props.displayItems || []).find((it) => it.id === val)
      if (match) query.value = chipLabel(match)
    }
  },
)

// React to async-loaded label coming in after mount.
watch(
  () => props.displayItems,
  (items) => {
    if (props.multi || !props.modelValue) return
    if (open.value) return
    const match = (items || []).find((it) => it.id === props.modelValue)
    if (match) query.value = chipLabel(match)
  },
  { deep: true },
)

defineExpose({ focusInput })
</script>
