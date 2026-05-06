<template>
  <div class="flex flex-col gap-3">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <p class="text-sm text-slate-600 dark:text-slate-300 font-medium">
          Simulierte Objekte
        </p>
        <p class="text-xs text-slate-400 mt-0.5">
          Boolean- und Integer-Datenpunkte auswählen — Bindings werden automatisch erstellt/entfernt.
        </p>
      </div>
      <button @click="load" class="btn-secondary btn-sm" :disabled="loading">
        <Spinner v-if="loading" size="xs" color="slate" />
        Aktualisieren
      </button>
    </div>

    <!-- Error -->
    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
      {{ error }}
    </div>

    <!-- Search -->
    <input
      v-if="items.length"
      v-model="search"
      type="text"
      class="input text-sm"
      placeholder="Datenpunkte suchen…"
    />

    <!-- List -->
    <div v-if="items.length" class="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      <div class="px-3 py-2 bg-slate-50 dark:bg-slate-800/60 flex items-center justify-between text-sm">
        <label class="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            :checked="allFilteredSelected"
            :indeterminate="someFilteredSelected && !allFilteredSelected"
            @change="toggleAll($event.target.checked)"
            class="w-4 h-4 rounded"
          />
          <span class="text-slate-600 dark:text-slate-300">Alle auswählen</span>
        </label>
        <span class="text-slate-400 text-xs">{{ filteredItems.length }} Objekte</span>
      </div>

      <div class="max-h-80 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700/50">
        <label
          v-for="item in filteredItems"
          :key="item.id"
          class="flex gap-3 px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer"
        >
          <input
            type="checkbox"
            :checked="selected.has(item.id)"
            @change="toggle(item.id, $event.target.checked)"
            class="w-4 h-4 rounded mt-0.5"
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-sm text-slate-700 dark:text-slate-200 truncate">{{ item.name }}</span>
              <Badge :variant="item.data_type === 'BOOLEAN' ? 'success' : 'info'" size="xs">
                {{ item.data_type }}
              </Badge>
              <Badge v-if="item.has_binding" variant="muted" size="xs">verknüpft</Badge>
            </div>
          </div>
        </label>
      </div>
    </div>

    <div v-else-if="!loading && !error" class="text-sm text-slate-400 text-center py-4">
      Keine Boolean- oder Integer-Objekte gefunden.
    </div>

    <!-- Save button -->
    <div v-if="items.length" class="flex items-center gap-3">
      <button
        @click="save"
        class="btn-primary btn-sm"
        :disabled="saving || !isDirty"
      >
        <Spinner v-if="saving" size="xs" color="white" />
        Verknüpfungen speichern
      </button>
      <span v-if="saveResult" :class="saveResult.success ? 'text-green-400' : 'text-red-400'" class="text-sm">
        {{ saveResult.message }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { adapterApi } from '@/api/client'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  instanceId: { type: String, required: true },
})

const items   = ref([])
const search  = ref('')
const loading = ref(false)
const saving  = ref(false)
const error   = ref(null)
const saveResult = ref(null)

// Set of currently selected DataPoint IDs
const selected = ref(new Set())
// Set of IDs that had bindings when we last loaded (server state)
const serverBound = ref(new Set())

const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(i => i.name.toLowerCase().includes(q))
})

const allFilteredSelected = computed(() =>
  filteredItems.value.length > 0 && filteredItems.value.every(i => selected.value.has(i.id))
)

const someFilteredSelected = computed(() =>
  filteredItems.value.some(i => selected.value.has(i.id))
)

const isDirty = computed(() => {
  const sel = selected.value
  const srv = serverBound.value
  if (sel.size !== srv.size) return true
  for (const id of sel) if (!srv.has(id)) return true
  return false
})

async function load() {
  loading.value = true
  error.value = null
  saveResult.value = null
  try {
    const { data } = await adapterApi.anwesenheitDatapoints(props.instanceId)
    items.value = data
    const boundSet = new Set(data.filter(i => i.has_binding).map(i => i.id))
    serverBound.value = boundSet
    selected.value = new Set(boundSet)
  } catch (e) {
    error.value = e.response?.data?.detail ?? 'Datenpunkte konnten nicht geladen werden'
  } finally {
    loading.value = false
  }
}

function toggle(id, checked) {
  const next = new Set(selected.value)
  if (checked) next.add(id)
  else next.delete(id)
  selected.value = next
}

function toggleAll(checked) {
  const next = new Set(selected.value)
  for (const item of filteredItems.value) {
    if (checked) next.add(item.id)
    else next.delete(item.id)
  }
  selected.value = next
}

async function save() {
  saving.value = true
  saveResult.value = null
  try {
    const { data } = await adapterApi.anwesenheitSyncBindings(props.instanceId, [...selected.value])
    saveResult.value = {
      success: true,
      message: `${data.created} erstellt, ${data.removed} entfernt${data.errors.length ? ` (${data.errors.length} Fehler)` : ''}`,
    }
    await load()  // Refresh to reflect new server state
  } catch (e) {
    saveResult.value = {
      success: false,
      message: e.response?.data?.detail ?? 'Speichern fehlgeschlagen',
    }
  } finally {
    saving.value = false
  }
}

watch(() => props.instanceId, () => { if (props.instanceId) load() })
onMounted(() => { if (props.instanceId) load() })
</script>
