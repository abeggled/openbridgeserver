<template>
  <div class="flex flex-col gap-5">
    <!-- Header -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Objekte</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ store.total }} Einträge</p>
      </div>
      <button @click="openCreate" class="btn-primary" data-testid="btn-new-datapoint">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        Neu
      </button>
    </div>

    <!-- Filter bar -->
    <div class="flex flex-wrap gap-3">
      <input
        v-model="filters.q"
        @input="onSearch"
        type="text"
        class="input flex-1 min-w-48"
        placeholder="Suche nach Name, UUID, Konfiguration …"
        data-testid="input-search"
      />
      <select
        v-model="filters.tag"
        @change="onSearch"
        class="input w-44"
        data-testid="select-tag"
      >
        <option value="">Alle Tags</option>
        <option v-for="t in availableTags" :key="t" :value="t">{{ t }}</option>
      </select>
      <select
        v-model="filters.type"
        @change="onSearch"
        class="input w-36"
        data-testid="select-type"
      >
        <option value="">Alle Typen</option>
        <option v-for="dt in store.datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
      </select>
      <select
        v-model="filters.quality"
        @change="onSearch"
        class="input w-36"
        data-testid="select-quality"
      >
        <option value="">Alle Qualitäten</option>
        <option value="good">Gut</option>
        <option value="uncertain">Unbekannt</option>
        <option value="bad">Schlecht</option>
      </select>
    </div>

    <!-- Table -->
    <div class="card overflow-hidden">
      <div v-if="store.loading && !store.items.length" class="flex justify-center py-12">
        <Spinner size="lg" />
      </div>
      <div v-else-if="!store.items.length" class="text-center text-slate-500 py-12 text-sm">
        Keine Objekte gefunden
      </div>
      <div v-else class="table-wrap">
        <table class="table" data-testid="datapoint-list">
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
              <th>Qualität</th>
              <th class="w-20"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="dp in store.items" :key="dp.id" :data-testid="'dp-row-' + dp.id">
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
              <td><Badge :variant="qualityVariant(liveQuality(dp))" dot size="xs">{{ qualityLabel(liveQuality(dp)) ?? '—' }}</Badge></td>
              <td>
                <div class="flex items-center gap-1">
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

        <!-- Infinite scroll sentinel -->
        <div ref="sentinelEl" class="h-1" />
      </div>
    </div>

    <!-- Loading more indicator -->
    <div v-if="store.loading && store.items.length" class="flex justify-center py-4">
      <Spinner size="sm" />
    </div>

    <!-- End of list -->
    <div v-if="!store.hasMore && store.items.length > 0 && !store.loading" class="text-center text-slate-400 text-xs py-2">
      Alle {{ store.total }} Einträge geladen
    </div>

    <!-- Create / Edit Modal -->
    <Modal v-model="showForm" :title="editTarget ? 'Objekt bearbeiten' : 'Neues Objekt'">
      <DataPointForm :initial="editTarget" :datatypes="store.datatypes" :save-handler="onSave" @cancel="showForm = false" />
    </Modal>

    <!-- Delete confirm -->
    <ConfirmDialog v-model="showConfirm" title="Objekt löschen"
      :message="`'${deleteTarget?.name}' und alle Verknüpfungen löschen?`"
      confirm-label="Löschen" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useDatapointStore } from '@/stores/datapoints'
import { useWebSocketStore } from '@/stores/websocket'
import Badge         from '@/components/ui/Badge.vue'
import Spinner       from '@/components/ui/Spinner.vue'
import Modal         from '@/components/ui/Modal.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import DataPointForm from '@/components/datapoints/DataPointForm.vue'

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

const filters      = ref({ q: '', tag: '', quality: '', type: '' })
const showForm     = ref(false)
const showConfirm  = ref(false)
const editTarget   = ref(null)
const deleteTarget = ref(null)
const sentinelEl   = ref(null)

let searchTimeout = null
let observer      = null
let unsubWs       = null

// Distinct tags from the currently loaded result set — populates the tag dropdown.
const availableTags = computed(() =>
  [...new Set(store.items.flatMap(dp => dp.tags))].sort()
)

// --------------------------------------------------------------------------
// Lifecycle
// --------------------------------------------------------------------------

onMounted(async () => {
  await store.loadDatatypes()

  const saved = store.restoreScrollState()
  if (saved) {
    // Restore filters first so the search uses them.
    Object.assign(filters.value, saved.filters)
    store.clearScrollState()
    // Load enough items to roughly match the saved scroll position.
    // One request with size=saved.count covers it without multiple round trips.
    await store.search(
      { ...filters.value },
      false,
    )
    // If the previous session had loaded multiple pages, keep loading until
    // we reach the saved count (cap at 5 pages to avoid runaway fetches).
    let pages = 0
    while (store.items.length < saved.count && store.hasMore && pages < 4) {
      await store.loadMore()
      pages++
    }
    await nextTick()
    window.scrollTo({ top: saved.scrollY, behavior: 'instant' })
  } else {
    await store.search(filters.value, false)
  }

  unsubWs = ws.onValue((id, value, quality) => store.patchValue(id, value, quality))
  _setupObserver()
})

onUnmounted(() => {
  unsubWs?.()
  observer?.disconnect()
})

// Save scroll state when navigating to a detail view.
onBeforeRouteLeave((to) => {
  if (to.name === 'DataPointDetail') {
    store.saveScrollState(window.scrollY, { ...filters.value })
  }
})

// Re-subscribe WebSocket whenever the loaded items list changes.
watch(() => store.items, (items) => {
  ws.subscribe(items.map(d => d.id))
}, { immediate: true })

// --------------------------------------------------------------------------
// Infinite scroll
// --------------------------------------------------------------------------

function _makeObserver() {
  // The app layout scrolls <main>, not the window.
  // Use that element as the root so IntersectionObserver fires correctly.
  const root = document.querySelector('main') ?? null
  return new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting && store.hasMore && !store.loading) {
        store.loadMore()
      }
    },
    { root, rootMargin: '300px' }
  )
}

function _setupObserver() {
  if (!sentinelEl.value) return
  observer = _makeObserver()
  observer.observe(sentinelEl.value)
}

// Reattach observer once the sentinel is actually in the DOM.
watch(sentinelEl, (el) => {
  observer?.disconnect()
  if (el) {
    observer = _makeObserver()
    observer.observe(el)
  }
})

// --------------------------------------------------------------------------
// Search / filter
// --------------------------------------------------------------------------

function onSearch() {
  clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    store.search({ ...filters.value }, false)
  }, 350)
}

// --------------------------------------------------------------------------
// CRUD handlers
// --------------------------------------------------------------------------

function openCreate() { editTarget.value = null; showForm.value = true }
function openEdit(dp) { editTarget.value = dp;   showForm.value = true }

async function onSave(payload) {
  if (editTarget.value) await store.update(editTarget.value.id, payload)
  else await store.create(payload)
  showForm.value = false
}

function confirmDelete(dp) { deleteTarget.value = dp; showConfirm.value = true }
async function doDelete()  { await store.remove(deleteTarget.value.id) }

// --------------------------------------------------------------------------
// Live value helpers
// --------------------------------------------------------------------------

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
function qualityLabel(q) {
  return q === 'good' ? 'Gut' : q === 'bad' ? 'Schlecht' : q === 'uncertain' ? 'Unbekannt' : q
}
</script>
