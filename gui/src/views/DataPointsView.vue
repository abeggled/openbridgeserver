<template>
  <div class="flex flex-col gap-5">
    <!-- Header -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">DataPoints</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ store.total }} Einträge</p>
      </div>
      <button @click="openCreate" class="btn-primary">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        Neu
      </button>
    </div>

    <!-- Search bar -->
    <div class="flex flex-wrap gap-3">
      <input v-model="filters.q" @input="onSearch" type="text" class="input flex-1 min-w-48" placeholder="Suche nach Name …" />
      <input v-model="filters.tag" @input="onSearch" type="text" class="input w-36" placeholder="Tag" />
      <select v-model="filters.type" @change="onSearch" class="input w-36">
        <option value="">Alle Typen</option>
        <option v-for="dt in store.datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
      </select>
    </div>

    <!-- Table -->
    <div class="card overflow-hidden">
      <div v-if="store.loading" class="flex justify-center py-12"><Spinner size="lg" /></div>
      <div v-else-if="!store.items.length" class="text-center text-slate-500 py-12 text-sm">Keine DataPoints gefunden</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th @click="store.setSort('name')" class="cursor-pointer select-none hover:text-blue-500 transition-colors">
                Name <SortIcon col="name" :active="store.sortCol" :dir="store.sortDir" />
              </th>
              <th @click="store.setSort('data_type')" class="cursor-pointer select-none hover:text-blue-500 transition-colors">
                Typ <SortIcon col="data_type" :active="store.sortCol" :dir="store.sortDir" />
              </th>
              <th>Tags</th>
              <th>Wert</th>
              <th>Quality</th>
              <th>MQTT Topic</th>
              <th class="w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="dp in store.items" :key="dp.id">
              <td class="font-medium">
                <RouterLink :to="`/datapoints/${dp.id}`" class="hover:text-blue-400 transition-colors">{{ dp.name }}</RouterLink>
              </td>
              <td><Badge variant="info" size="xs">{{ dp.data_type }}</Badge></td>
              <td>
                <div class="flex flex-wrap gap-1">
                  <Badge v-for="t in dp.tags" :key="t" variant="default" size="xs">{{ t }}</Badge>
                </div>
              </td>
              <td class="font-mono text-sm text-blue-500 dark:text-blue-300">{{ liveValue(dp) }}</td>
              <td><Badge :variant="qualityVariant(liveQuality(dp))" dot size="xs">{{ liveQuality(dp) ?? '—' }}</Badge></td>
              <td class="font-mono text-xs text-slate-500 max-w-xs truncate">{{ dp.mqtt_topic }}</td>
              <td>
                <div class="flex items-center gap-1">
                  <RouterLink :to="`/datapoints/${dp.id}`" class="btn-icon" title="Detail">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                  </RouterLink>
                  <button @click="openEdit(dp)" class="btn-icon" title="Bearbeiten">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                  </button>
                  <button @click="confirmDelete(dp)" class="btn-icon text-red-400" title="Löschen">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div v-if="store.pages > 1" class="flex items-center justify-between">
      <span class="text-sm text-slate-500">Seite {{ store.page + 1 }} von {{ store.pages }}</span>
      <div class="flex gap-2">
        <button @click="goPage(store.page - 1)" :disabled="store.page === 0" class="btn-secondary btn-sm">‹</button>
        <button @click="goPage(store.page + 1)" :disabled="store.page >= store.pages - 1" class="btn-secondary btn-sm">›</button>
      </div>
    </div>

    <!-- Create / Edit Modal -->
    <Modal v-model="showForm" :title="editTarget ? 'DataPoint bearbeiten' : 'Neuer DataPoint'">
      <DataPointForm :initial="editTarget" :datatypes="store.datatypes" :save-handler="onSave" @cancel="showForm = false" />
    </Modal>

    <!-- Delete confirm -->
    <ConfirmDialog v-model="showConfirm" title="DataPoint löschen"
      :message="`'${deleteTarget?.name}' und alle Bindings löschen?`"
      confirm-label="Löschen" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useDatapointStore } from '@/stores/datapoints'
import { useWebSocketStore } from '@/stores/websocket'
import Badge          from '@/components/ui/Badge.vue'
import Spinner        from '@/components/ui/Spinner.vue'
import Modal          from '@/components/ui/Modal.vue'
import ConfirmDialog  from '@/components/ui/ConfirmDialog.vue'
import DataPointForm  from '@/components/datapoints/DataPointForm.vue'

// Inline sort-indicator component
const SortIcon = {
  props: ['col', 'active', 'dir'],
  template: `<span class="inline-block ml-0.5 opacity-40" :class="{ 'opacity-100 text-blue-500': active === col }">
    <svg v-if="active !== col" class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4"/></svg>
    <svg v-else-if="dir === 'asc'"  class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
    <svg v-else                     class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
  </span>`,
}

const store = useDatapointStore()
const ws    = useWebSocketStore()

const filters     = ref({ q: '', tag: '', type: '' })
const showForm    = ref(false)
const showConfirm = ref(false)
const editTarget  = ref(null)
const deleteTarget = ref(null)
let searchTimeout = null
let unsubWs = null

onMounted(async () => {
  await store.loadDatatypes()
  await store.fetchPage(0)
  unsubWs = ws.onValue((id, value, quality) => store.patchValue(id, value, quality))
})
onUnmounted(() => unsubWs?.())

watch(() => store.items, (items) => {
  ws.subscribe(items.map(d => d.id))
}, { immediate: true })

function onSearch() {
  clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    const { q, tag, type } = filters.value
    if (q || tag || type) store.search({ q, tag, type })
    else store.fetchPage(0)
  }, 350)
}

function goPage(p) {
  const { q, tag, type } = filters.value
  store.search({ q, tag, type, page: p })
}

function openCreate() { editTarget.value = null; showForm.value = true }
function openEdit(dp) { editTarget.value = dp;   showForm.value = true }

async function onSave(payload) {
  if (editTarget.value) await store.update(editTarget.value.id, payload)
  else await store.create(payload)
  showForm.value = false   // only reached if no error thrown
}

function confirmDelete(dp) { deleteTarget.value = dp; showConfirm.value = true }
async function doDelete()  { await store.remove(deleteTarget.value.id) }

function liveValue(dp) {
  const live = ws.liveValues[dp.id]
  const v    = live?.value ?? dp.value
  if (v === null || v === undefined) return '—'
  return dp.unit ? `${v} ${dp.unit}` : String(v)
}
function liveQuality(dp) { return ws.liveValues[dp.id]?.quality ?? dp.quality }
function qualityVariant(q) {
  return q === 'good' ? 'success' : q === 'bad' ? 'danger' : q === 'uncertain' ? 'warning' : 'muted'
}
</script>
