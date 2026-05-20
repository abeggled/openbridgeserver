<template>
  <div class="flex flex-col gap-5">
    <div class="flex flex-wrap items-start gap-3">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Logs</h2>
        <p class="text-sm text-slate-500 mt-0.5">Applikations-Logs — Live</p>
      </div>
      <select v-model="logLevel" @change="setLevel" class="input w-36" title="Log-Level zur Laufzeit ändern">
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
      <button @click="load" class="btn-secondary btn-sm">↻ Aktualisieren</button>
      <span :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium',
        wsConnected ? 'bg-teal-500/15 text-teal-600 dark:text-teal-400' : 'bg-slate-200/50 dark:bg-slate-700/50 text-slate-500']"
        data-testid="status-badge">
        <span :class="['w-1.5 h-1.5 rounded-full', wsConnected ? 'bg-teal-400 animate-pulse' : 'bg-slate-600']" />
        {{ wsConnected ? 'Live' : 'Offline' }}
      </span>
    </div>

    <!-- Filters -->
    <div class="flex flex-wrap gap-3">
      <input v-model="filters.q" type="text" class="input flex-1 min-w-40"
        placeholder="Suche in Logger-Name oder Meldung …" @input="debouncedLoad" data-testid="input-filter" />
      <select v-model="filters.level" class="input w-36" @change="load">
        <option value="">Alle Level</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
        <option value="CRITICAL">CRITICAL</option>
      </select>
      <select v-model="filters.limit" class="input w-28" @change="load">
        <option value="100">100</option>
        <option value="200">200</option>
        <option value="500">500</option>
      </select>
    </div>

    <!-- Log table -->
    <div class="card overflow-hidden">
      <div v-if="loading" class="flex justify-center py-12"><Spinner size="lg" /></div>
      <div v-else-if="!entries.length" class="text-center text-slate-500 text-sm py-12">Keine Log-Einträge vorhanden</div>
      <div v-else class="table-wrap max-h-[65vh] overflow-y-auto">
        <table class="table">
          <thead class="sticky top-0">
            <tr>
              <th class="w-44">Zeitstempel</th>
              <th class="w-24">Level</th>
              <th class="w-56">Logger</th>
              <th>Meldung</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(e, i) in entries" :key="i" data-testid="log-entry">
              <td class="font-mono text-xs text-slate-400 whitespace-nowrap">{{ fmtDateTime(e.ts) }}</td>
              <td><Badge :variant="levelVariant(e.level)" size="xs">{{ e.level }}</Badge></td>
              <td class="font-mono text-xs text-slate-400 truncate max-w-[14rem]" :title="e.logger">{{ e.logger }}</td>
              <td class="text-sm break-all">{{ e.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { logsApi } from '@/api/client'
import { useTz } from '@/composables/useTz'
import { useWebSocketStore } from '@/stores/websocket'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'

const { fmtDateTime } = useTz()
const wsStore = useWebSocketStore()

const entries = ref([])
const loading = ref(false)
const logLevel = ref('INFO')

const filters = reactive({ q: '', level: '', limit: '200' })

const wsConnected = computed(() => wsStore.connected)

let debounceTimer  = null
let unregisterLog  = null

function debouncedLoad() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(load, 350)
}

function levelVariant(level) {
  return { DEBUG: 'muted', INFO: 'info', WARNING: 'warning', ERROR: 'danger', CRITICAL: 'danger' }[level] ?? 'muted'
}

/** Live push from WS — prepend and honour client-side filters */
function onLiveEntry(entry) {
  const q = filters.q.toLowerCase()
  if (q && !(entry.logger?.toLowerCase().includes(q) || entry.message?.toLowerCase().includes(q))) return
  if (filters.level && entry.level !== filters.level) return
  entries.value = [entry, ...entries.value].slice(0, parseInt(filters.limit) || 200)
}

onMounted(async () => {
  try {
    const { data } = await logsApi.getLevel()
    logLevel.value = data.level
  } catch {
    // non-admin — leave default
  }
  await load()
  unregisterLog = wsStore.onLogEntry(onLiveEntry)
})

onUnmounted(() => {
  unregisterLog?.()
  clearTimeout(debounceTimer)
})

async function load() {
  loading.value = true
  try {
    const params = {}
    if (filters.level) params.level = filters.level
    params.limit = parseInt(filters.limit) || 200
    const { data } = await logsApi.list(params)
    // Client-side text filter (server only filters by level)
    const q = filters.q.toLowerCase()
    entries.value = q
      ? data.filter(e => e.logger?.toLowerCase().includes(q) || e.message?.toLowerCase().includes(q))
      : data
  } finally {
    loading.value = false
  }
}

async function setLevel() {
  try {
    await logsApi.setLevel(logLevel.value)
  } catch {
    // non-admin users get a 403 — silently ignore
  }
}
</script>
