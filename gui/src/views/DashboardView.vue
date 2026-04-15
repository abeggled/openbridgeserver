<template>
  <div class="flex flex-col gap-6">
    <!-- Header -->
    <div>
      <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Übersicht</h2>
      <p class="text-sm text-slate-500 mt-0.5">Systemübersicht · Live-Status</p>
    </div>

    <!-- Stat cards -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard label="Objekte" :value="health.datapoints" icon="📋" color="blue" />
      <StatCard label="Aktive Adapter Instanzen" :value="health.adapters_running" icon="🔌" color="green" />
      <StatCard label="WS-Status" :value="ws.connected ? 'Live' : 'Offline'" icon="⚡" :color="ws.connected ? 'green' : 'red'" />
      <StatCard label="Server" :value="health.status === 'ok' ? 'Online' : 'Fehler'" icon="🖥️" :color="health.status === 'ok' ? 'green' : 'red'" />
    </div>

    <!-- Adapter status + recent values -->
    <div class="grid lg:grid-cols-2 gap-4">
      <!-- Adapters -->
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">Adapter Status</h3>
          <RouterLink to="/adapters" class="text-xs text-blue-400 hover:underline">Alle →</RouterLink>
        </div>
        <div class="card-body flex flex-col gap-2">
          <div v-if="adaptersLoading" class="flex justify-center py-4"><Spinner /></div>
          <div v-else-if="!adapters.length" class="text-center text-slate-500 text-sm py-4">Keine Adapter konfiguriert</div>
          <div v-for="a in adapters" :key="a.adapter_type"
               class="flex items-center gap-3 p-3 bg-surface-700 rounded-lg">
            <span :class="['w-2.5 h-2.5 rounded-full shrink-0', a.connected ? 'bg-green-400' : a.running ? 'bg-amber-400' : 'bg-slate-600']" />
            <span class="flex-1 text-sm font-medium text-slate-700 dark:text-slate-200">{{ a.adapter_type }}</span>
            <Badge :variant="a.connected ? 'success' : a.running ? 'warning' : 'muted'" size="xs">
              {{ a.connected ? 'Verbunden' : a.running ? 'Läuft' : 'Inaktiv' }}
            </Badge>
            <span class="text-xs text-slate-500">{{ a.bindings }} Verknüpfungen</span>
          </div>
        </div>
      </div>

      <!-- Recent live values -->
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">Live-Werte</h3>
          <RouterLink to="/datapoints" class="text-xs text-blue-400 hover:underline">Alle →</RouterLink>
        </div>
        <div class="card-body flex flex-col gap-0 -mx-5 -my-5 overflow-hidden rounded-b-xl">
          <div v-if="dpStore.loading" class="flex justify-center py-8"><Spinner /></div>
          <div v-else-if="!dpStore.items.length" class="text-center text-slate-500 text-sm py-8">Keine Objekte vorhanden</div>
          <template v-else>
            <div v-for="dp in dpStore.items.slice(0, 10)" :key="dp.id"
                 class="flex items-center gap-3 px-5 py-2.5 border-b border-slate-200/60 dark:border-slate-700/40 last:border-0 hover:bg-slate-100/80 dark:hover:bg-slate-800/40 transition-colors">
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium text-slate-700 dark:text-slate-200 truncate">{{ dp.name }}</div>
                <div class="text-xs text-slate-500 font-mono truncate">{{ dp.mqtt_topic }}</div>
              </div>
              <div class="text-right shrink-0">
                <div :class="['text-sm font-mono font-medium', liveClass(dp)]">
                  {{ displayValue(dp) }}
                </div>
                <Badge :variant="qualityVariant(dp.quality)" size="xs" dot>{{ dp.quality ?? '—' }}</Badge>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { systemApi } from '@/api/client'
import { useDatapointStore } from '@/stores/datapoints'
import { useWebSocketStore } from '@/stores/websocket'
import { useAdapterStore } from '@/stores/adapters'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatCard from '@/components/ui/StatCard.vue'

const dpStore  = useDatapointStore()
const ws       = useWebSocketStore()
const adStore  = useAdapterStore()

const health   = ref({ status: '…', datapoints: '…', adapters_running: '…' })
const adapters = ref([])
const adaptersLoading = ref(false)

let unsubWs = null

onMounted(async () => {
  // Health (no auth)
  try { const { data } = await systemApi.health(); health.value = data } catch {}

  // DataPoints
  if (!dpStore.items.length) await dpStore.search({}, false)

  // Adapters
  adaptersLoading.value = true
  try { await adStore.fetchAdapters(); adapters.value = adStore.adapters } finally { adaptersLoading.value = false }

  // Subscribe all datapoints for live updates
  const ids = dpStore.items.map(d => d.id)
  ws.subscribe(ids)

  unsubWs = ws.onValue((id, value, quality) => dpStore.patchValue(id, value, quality))
})

onUnmounted(() => { unsubWs?.() })

function displayValue(dp) {
  const live = ws.liveValues[dp.id]
  const val  = live?.value ?? dp.value
  if (val === null || val === undefined) return '—'
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (dp.unit) return `${val} ${dp.unit}`
  return String(val)
}

function liveClass(dp) {
  return ws.liveValues[dp.id] ? 'text-blue-500 dark:text-blue-300' : 'text-slate-600 dark:text-slate-300'
}

function qualityVariant(q) {
  if (q === 'good')      return 'success'
  if (q === 'bad')       return 'danger'
  if (q === 'uncertain') return 'warning'
  return 'muted'
}
</script>
