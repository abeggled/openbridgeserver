<script setup lang="ts">
/**
 * DataPointPicker — Suchfeld mit Dropdown für DataPoints
 * Ruft /api/v1/search auf und gibt die gewählte ID zurück.
 */
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import { datapoints } from '@/api/client'
import type { DataPoint } from '@/types'

const props = defineProps<{
  modelValue: string | null   // DataPoint-UUID
  label?: string
  /** Kompatible Datentypen — leeres Array oder ['*'] zeigt alle */
  compatibleTypes?: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', id: string | null): void
  (e: 'select', dp: DataPoint): void
}>()

const query = ref('')
const results = ref<DataPoint[]>([])
const open = ref(false)
const loading = ref(false)
const selectedName = ref('')
const inputEl = ref<HTMLInputElement | null>(null)
const dropdownEl = ref<HTMLElement | null>(null)

// DP-Namen laden: beim Mount und bei jeder Änderung von modelValue
async function loadName(id: string | null) {
  if (!id) { selectedName.value = ''; return }
  try {
    const dp = await datapoints.get(id)
    selectedName.value = dp.name
  } catch {
    selectedName.value = id
  }
}

onMounted(() => loadName(props.modelValue))
watch(() => props.modelValue, (id) => loadName(id))

// Ob alle Typen erlaubt sind
const allTypesAllowed = computed(() =>
  !props.compatibleTypes || props.compatibleTypes.length === 0 || props.compatibleTypes.includes('*')
)

// Einzelner Typ für Backend-Filter (nur wenn exakt ein Typ ohne Wildcard)
const singleTypeFilter = computed(() => {
  if (allTypesAllowed.value) return ''
  if (props.compatibleTypes!.length === 1) return props.compatibleTypes![0]
  return ''
})

let debounce: ReturnType<typeof setTimeout> | null = null

watch(query, (val) => {
  if (debounce) clearTimeout(debounce)
  if (!val.trim()) { results.value = []; return }
  loading.value = true
  debounce = setTimeout(async () => {
    try {
      const res = await datapoints.search(val, 0, 50, singleTypeFilter.value)
      // Bei mehreren kompatiblen Typen: client-seitig filtern
      results.value = allTypesAllowed.value
        ? res.items
        : res.items.filter(dp => props.compatibleTypes!.includes(dp.data_type))
    } finally {
      loading.value = false
    }
  }, 250)
})

function openSearch() {
  open.value = true
  query.value = ''
  results.value = []
  setTimeout(() => inputEl.value?.focus(), 50)
}

function select(dp: DataPoint) {
  selectedName.value = dp.name
  open.value = false
  query.value = ''
  emit('update:modelValue', dp.id)
  emit('select', dp)
}

function clear() {
  selectedName.value = ''
  emit('update:modelValue', null)
}

// Click-outside schliesst Dropdown
function onDocClick(e: MouseEvent) {
  if (dropdownEl.value && !dropdownEl.value.contains(e.target as Node)) {
    open.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
  <div class="relative" ref="dropdownEl">
    <label v-if="label" class="block text-xs text-gray-400 mb-1">{{ label }}</label>

    <!-- Anzeige: aktuell gewählter DP -->
    <div
      v-if="!open"
      class="flex items-center gap-2 w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 cursor-pointer hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
      @click="openSearch"
    >
      <span
        class="flex-1 text-sm truncate"
        :class="selectedName ? 'text-gray-900 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'"
        :title="selectedName || undefined"
      >
        {{ selectedName || 'DataPoint wählen …' }}
      </span>
      <button
        v-if="selectedName"
        class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 text-xs shrink-0"
        @click.stop="clear"
      >✕</button>
      <span class="text-gray-400 dark:text-gray-500 text-xs shrink-0">▾</span>
    </div>

    <!-- Suchfeld (wenn offen) -->
    <div v-else class="flex flex-col border border-blue-500 rounded bg-white dark:bg-gray-800 overflow-hidden">
      <input
        ref="inputEl"
        v-model="query"
        type="text"
        placeholder="Name, Tag oder Typ …"
        class="w-full bg-transparent px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none"
        @keydown.escape="open = false"
      />

      <!-- Ergebnisse -->
      <div class="max-h-52 overflow-y-auto border-t border-gray-200 dark:border-gray-700">
        <div v-if="loading" class="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">Suche …</div>
        <div v-else-if="query && results.length === 0" class="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">
          Keine Treffer
        </div>
        <button
          v-for="dp in results"
          :key="dp.id"
          class="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-left"
          :title="dp.name"
          @click="select(dp)"
        >
          <span class="flex-1 min-w-0">
            <span class="block text-sm text-gray-900 dark:text-gray-100 truncate">{{ dp.name }}</span>
            <span class="block text-xs text-gray-400 dark:text-gray-500">{{ dp.data_type }}{{ dp.unit ? ' · ' + dp.unit : '' }}</span>
          </span>
        </button>
      </div>
    </div>
  </div>
</template>
