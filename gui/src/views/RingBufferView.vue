<template>
  <div class="flex flex-col gap-5">
    <div class="flex flex-wrap items-start gap-3">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-100">RingBuffer</h2>
        <p class="text-sm text-slate-500 mt-0.5">Debug-Log — letzte Wertänderungen</p>
      </div>
      <button @click="showConfig = true" class="btn-secondary btn-sm">⚙ Konfigurieren</button>
      <button @click="load" class="btn-secondary btn-sm">↻ Aktualisieren</button>
      <span :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium',
        wsConnected ? 'bg-teal-500/15 text-teal-400' : 'bg-slate-700/50 text-slate-500']">
        <span :class="['w-1.5 h-1.5 rounded-full', wsConnected ? 'bg-teal-400 animate-pulse' : 'bg-slate-600']" />
        {{ wsConnected ? 'Live' : 'Offline' }}
      </span>
    </div>

    <!-- Stats bar -->
    <div v-if="stats" class="grid grid-cols-3 gap-3">
      <div class="card p-4 text-center">
        <div class="text-2xl font-bold text-slate-100">{{ stats.total }}</div>
        <div class="text-xs text-slate-500 mt-1">Einträge</div>
      </div>
      <div class="card p-4 text-center">
        <div class="text-2xl font-bold text-slate-100">{{ stats.max_entries }}</div>
        <div class="text-xs text-slate-500 mt-1">Max. Kapazität</div>
      </div>
      <div class="card p-4 text-center">
        <Badge :variant="stats.storage === 'memory' ? 'info' : 'warning'" class="text-base">{{ stats.storage }}</Badge>
        <div class="text-xs text-slate-500 mt-2">Speicher</div>
      </div>
    </div>

    <!-- Filters -->
    <div class="flex flex-wrap gap-3">
      <input v-model="filters.q" type="text" class="input flex-1 min-w-40" placeholder="Suche nach Name/ID …" @input="debouncedLoad" />
      <input v-model="filters.adapter" type="text" class="input w-36" placeholder="Adapter" @input="debouncedLoad" />
      <select v-model="filters.limit" class="input w-28" @change="load">
        <option value="100">100</option>
        <option value="500">500</option>
        <option value="1000">1000</option>
      </select>
    </div>

    <!-- Log table -->
    <div class="card overflow-hidden">
      <div v-if="loading" class="flex justify-center py-12"><Spinner size="lg" /></div>
      <div v-else-if="!entries.length" class="text-center text-slate-500 text-sm py-12">Keine Einträge im RingBuffer</div>
      <div v-else class="table-wrap max-h-[60vh] overflow-y-auto">
        <table class="table">
          <thead class="sticky top-0">
            <tr>
              <th>Zeitstempel</th>
              <th>DataPoint</th>
              <th>Wert</th>
              <th>Vorheriger Wert</th>
              <th>Quality</th>
              <th>Adapter</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(e, i) in entries" :key="i">
              <td class="font-mono text-xs text-slate-400 whitespace-nowrap">{{ fmtDateTime(e.ts) }}</td>
              <td class="text-sm">
                <RouterLink :to="`/datapoints/${e.datapoint_id}`" class="text-blue-400 hover:underline font-mono text-xs">
                  {{ e.name ?? e.datapoint_id?.slice(0, 8) }}
                </RouterLink>
              </td>
              <td class="font-mono text-sm text-blue-300">{{ e.new_value }}</td>
              <td class="font-mono text-sm text-slate-500">{{ e.old_value ?? '—' }}</td>
              <td><Badge :variant="e.quality === 'good' ? 'success' : 'warning'" size="xs" dot>{{ e.quality }}</Badge></td>
              <td class="text-xs text-slate-500">{{ e.source_adapter ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Config modal -->
    <Modal v-model="showConfig" title="RingBuffer konfigurieren" max-width="sm">
      <form @submit.prevent="saveConfig" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">Speicher</label>
          <select v-model="configForm.storage" class="input">
            <option value="memory">memory (RAM, kein Neustart-Persistenz)</option>
            <option value="disk">disk (SQLite, persistent)</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Max. Einträge</label>
          <input v-model.number="configForm.max_entries" type="number" class="input" min="100" max="1000000" step="1000" />
        </div>
        <div v-if="configMsg" :class="['p-3 rounded-lg text-sm', configMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ configMsg.text }}</div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showConfig = false" class="btn-secondary">Schliessen</button>
          <button type="submit" class="btn-primary" :disabled="configSaving">
            <Spinner v-if="configSaving" size="sm" color="white" />
            Übernehmen
          </button>
        </div>
      </form>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ringbufferApi } from '@/api/client'
import { useTz } from '@/composables/useTz'
import { useWebSocketStore } from '@/stores/websocket'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Modal   from '@/components/ui/Modal.vue'

const { fmtDateTime } = useTz()
const wsStore = useWebSocketStore()

const entries      = ref([])
const stats        = ref(null)
const loading      = ref(false)
const showConfig   = ref(false)
const configSaving = ref(false)
const configMsg    = ref(null)

const filters    = reactive({ q: '', adapter: '', limit: '500' })
const configForm = reactive({ storage: 'memory', max_entries: 10000 })

// Computed: WS connection status
const wsConnected = computed(() => wsStore.connected)

let debounceTimer   = null
let unregisterRb    = null  // WS ringbuffer handler unsubscribe fn

function debouncedLoad() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(load, 350)
}

/** Called by WS on every live DataValueEvent — prepend to list respecting filters */
function onLiveEntry(entry) {
  // Apply client-side filter to avoid showing irrelevant entries
  const q = filters.q.toLowerCase()
  if (q && !(entry.name?.toLowerCase().includes(q) || entry.datapoint_id?.toLowerCase().includes(q))) return
  if (filters.adapter && entry.source_adapter !== filters.adapter) return

  entries.value = [entry, ...entries.value].slice(0, parseInt(filters.limit) || 500)
}

onMounted(async () => {
  await Promise.all([load(), loadStats()])
  if (stats.value) {
    configForm.storage     = stats.value.storage
    configForm.max_entries = stats.value.max_entries
  }
  // Subscribe to live ringbuffer pushes via existing WS connection
  unregisterRb = wsStore.onRingbufferEntry(onLiveEntry)
})

onUnmounted(() => {
  unregisterRb?.()
  clearTimeout(debounceTimer)
})

async function load() {
  loading.value = true
  try {
    const { data } = await ringbufferApi.query({
      q:       filters.q       || undefined,
      adapter: filters.adapter || undefined,
      limit:   parseInt(filters.limit),
    })
    entries.value = data
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try { const { data } = await ringbufferApi.stats(); stats.value = data } catch {}
}

async function saveConfig() {
  configSaving.value = true
  configMsg.value = null
  try {
    await ringbufferApi.config(configForm.storage, configForm.max_entries)
    configMsg.value = { ok: true, text: 'Konfiguration übernommen' }
    await loadStats()
  } catch (e) {
    configMsg.value = { ok: false, text: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    configSaving.value = false
  }
}
</script>
