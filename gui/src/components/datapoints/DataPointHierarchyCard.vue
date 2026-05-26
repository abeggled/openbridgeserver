<template>
  <div class="card">
    <div class="card-header">
      <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">Hierarchie-Zuordnungen</h3>
    </div>
    <div class="card-body flex flex-col gap-4">

      <!-- Aktuelle Zuordnungen -->
      <div v-if="linkedLoading" class="flex justify-center py-3"><Spinner size="sm" /></div>
      <div v-else-if="linked.length === 0" class="text-sm text-slate-500">
        Noch keinen Hierarchieknoten zugeordnet.
      </div>
      <div v-else class="flex flex-wrap gap-2">
        <div
          v-for="ref in linked" :key="ref.link_id"
          class="flex items-center gap-1 pl-2.5 pr-1.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-700 dark:text-blue-300 border border-blue-500/20"
          :title="nodeFullPath(ref)">
          <span class="text-blue-400 dark:text-blue-500 font-normal">{{ ref.tree_name }}</span>
          <template v-for="seg in (ref.node_path || [])" :key="seg.node_id">
            <svg class="w-2 h-2 text-blue-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
            <span class="text-blue-500 dark:text-blue-400 font-normal">{{ seg.node_name }}</span>
          </template>
          <svg class="w-2.5 h-2.5 text-blue-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
          </svg>
          <span>{{ ref.node_name }}</span>
          <button
            @click="removeLink(ref)"
            class="ml-0.5 text-blue-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
            title="Zuordnung entfernen">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Suche -->
      <div class="flex flex-col gap-2">
        <div class="relative">
          <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z"/>
          </svg>
          <input
            v-model="searchQ"
            type="text"
            class="input text-sm pl-8"
            placeholder="Knoten suchen — z.B. Raum, Heizung …"
            @input="onSearchInput"
          />
        </div>

        <!-- Suchergebnisse -->
        <div v-if="searchLoading" class="flex justify-center py-2"><Spinner size="sm" /></div>
        <div v-else-if="searchQ && results.length === 0" class="text-xs text-slate-500 text-center py-2">
          Keine Knoten gefunden.
        </div>
        <div v-else-if="results.length" class="flex flex-col gap-0.5 max-h-52 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg">
          <button
            v-for="node in results"
            :key="node.node_id"
            @click="addLink(node)"
            :disabled="isLinked(node.node_id)"
            :class="['flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors w-full',
              isLinked(node.node_id)
                ? 'opacity-40 cursor-not-allowed bg-slate-50 dark:bg-slate-800/40'
                : 'hover:bg-slate-50 dark:hover:bg-slate-700/40']">
            <span class="text-slate-400 text-xs font-medium shrink-0">{{ node.tree_name }}</span>
            <svg class="w-3 h-3 text-slate-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
            <span class="text-slate-700 dark:text-slate-200 flex-1 truncate">{{ node.node_name }}</span>
            <svg v-if="isLinked(node.node_id)" class="w-3.5 h-3.5 text-blue-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
            </svg>
            <svg v-else class="w-3.5 h-3.5 text-green-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Feedback -->
      <div v-if="feedback" :class="['text-xs px-2 py-1 rounded', feedback.ok ? 'text-green-500' : 'text-red-500']">
        {{ feedback.text }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { hierarchyApi } from '@/api/client.js'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  dpId: { type: String, required: true },
})

// ── State ────────────────────────────────────────────────────────────────

const linked       = ref([])   // NodeRef[]
const linkedLoading = ref(false)
const searchQ      = ref('')
const results      = ref([])   // NodeSearchResult[]
const searchLoading = ref(false)
const feedback     = ref(null)

let debounceTimer = null

// ── Load linked nodes ─────────────────────────────────────────────────────

async function loadLinked() {
  linkedLoading.value = true
  try {
    const { data } = await hierarchyApi.getDatapointNodes(props.dpId)
    linked.value = data
  } finally {
    linkedLoading.value = false
  }
}

// ── Search ────────────────────────────────────────────────────────────────

function onSearchInput() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(doSearch, 220)
}

async function doSearch() {
  if (!searchQ.value.trim()) {
    results.value = []
    return
  }
  searchLoading.value = true
  try {
    const { data } = await hierarchyApi.searchNodes(searchQ.value.trim(), 40)
    results.value = data
  } catch {
    results.value = []
  } finally {
    searchLoading.value = false
  }
}

// ── Link management ───────────────────────────────────────────────────────

function isLinked(nodeId) {
  return linked.value.some(l => l.node_id === nodeId)
}

async function addLink(node) {
  try {
    await hierarchyApi.createLink({ node_id: node.node_id, datapoint_id: props.dpId })
    await loadLinked()
    showFeedback(`${node.tree_name} › ${node.node_name} zugeordnet`, true)
  } catch (e) {
    showFeedback(e.response?.data?.detail || 'Fehler beim Zuordnen', false)
  }
}

async function removeLink(ref) {
  try {
    await hierarchyApi.deleteLink(ref.node_id, props.dpId)
    await loadLinked()
  } catch {
    showFeedback('Fehler beim Entfernen', false)
  }
}

// ── Utils ─────────────────────────────────────────────────────────────────

function nodeFullPath(ref) {
  const parts = [ref.tree_name, ...(ref.node_path || []).map(n => n.node_name), ref.node_name]
  return parts.join(' › ')
}

function showFeedback(text, ok) {
  feedback.value = { text, ok }
  setTimeout(() => { feedback.value = null }, 3000)
}

onMounted(loadLinked)
</script>
