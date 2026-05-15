<template>
  <div class="flex flex-col gap-5">

    <!-- Header -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Objekte</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ store.total }} Einträge</p>
      </div>
      <button @click="openCreate" class="btn-primary" data-testid="btn-new-datapoint">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
        Neu
      </button>
    </div>

    <!-- ── Filter-Leiste ── -->
    <div class="flex flex-col gap-2">

      <!-- Suchfeld -->
      <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z"/>
        </svg>
        <input
          v-model="filters.q"
          @input="onSearch"
          type="text"
          class="input pl-9 w-full"
          placeholder="Name, UUID, Konfiguration …"
          data-testid="input-search"
        />
        <button v-if="filters.q" @click="clearFilter('q')"
          class="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>

      <!-- Filter-Zeile -->
      <div class="flex flex-wrap gap-2 items-center">

        <!-- Typ -->
        <div class="relative">
          <select v-model="filters.type" @change="onSearch"
            :class="['input text-sm pr-7 appearance-none', filters.type ? 'border-blue-500 bg-blue-500/5 text-blue-600 dark:text-blue-400 font-medium' : '']"
            data-testid="select-type" style="min-width: 130px">
            <option value="">Alle Typen</option>
            <option v-for="dt in store.datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
          </select>
          <button v-if="filters.type" @click="clearFilter('type')"
            class="absolute right-1.5 top-1/2 -translate-y-1/2 text-blue-400 hover:text-blue-600">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <!-- Tag (Multi-Select) -->
        <div class="relative" ref="tagFilterRef" data-testid="tag-filter">
          <button
            @click="tagDropOpen = !tagDropOpen"
            :class="['input text-sm flex items-center gap-1.5 cursor-pointer select-none min-w-36',
              filters.tags.length ? 'border-blue-500 bg-blue-500/5' : '']">
            <svg class="w-3.5 h-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a2 2 0 014-4z"/>
            </svg>
            <span v-if="!filters.tags.length" class="text-slate-400 flex-1 text-left">Alle Tags</span>
            <span v-else class="text-blue-600 dark:text-blue-400 font-medium flex-1 text-left">
              {{ filters.tags.length === 1 ? filters.tags[0] : `${filters.tags.length} Tags` }}
            </span>
            <svg class="w-3 h-3 text-slate-400 shrink-0 transition-transform" :class="tagDropOpen ? 'rotate-180' : ''"
              fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>

          <!-- Tag dropdown -->
          <div v-if="tagDropOpen"
            class="absolute z-20 left-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl overflow-hidden"
            style="min-width: 160px">
            <div v-if="!store.allTags.length" class="text-xs text-slate-500 text-center py-3">Keine Tags vorhanden</div>
            <div v-else class="max-h-60 overflow-y-auto py-1">
              <button v-for="t in store.allTags" :key="t"
                @click="toggleTag(t)"
                class="w-full flex items-center gap-2.5 px-3 py-1.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                <span :class="['flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-colors',
                  filters.tags.includes(t)
                    ? 'bg-blue-500 border-blue-500'
                    : 'border-slate-300 dark:border-slate-600']">
                  <svg v-if="filters.tags.includes(t)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
                  </svg>
                </span>
                <span :class="filters.tags.includes(t) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-slate-700 dark:text-slate-200'">{{ t }}</span>
              </button>
            </div>
            <div v-if="filters.tags.length" class="border-t border-slate-100 dark:border-slate-700 p-1.5">
              <button @click="clearFilter('tags')"
                class="w-full text-xs text-center text-slate-500 hover:text-red-500 transition-colors py-1">
                Auswahl aufheben
              </button>
            </div>
          </div>
        </div>

        <!-- Qualität -->
        <div class="flex gap-1">
          <button v-for="q in qualityOptions" :key="q.value"
            @click="toggleQuality(q.value)"
            :class="['px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors',
              filters.quality === q.value
                ? q.activeClass
                : 'border-slate-200 dark:border-slate-700 text-slate-500 hover:border-slate-300 dark:hover:border-slate-600']"
            :data-testid="`btn-quality-${q.value}`">
            <span class="inline-block w-1.5 h-1.5 rounded-full mr-1" :class="q.dot" />{{ q.label }}
          </button>
        </div>

        <!-- Hierarchieknoten-Filter (Multi-Select mit Suche, inkl. Baum-Filter) -->
        <div class="relative" ref="nodeFilterRef" data-testid="node-filter">
          <button
            @click="nodeDropOpen = !nodeDropOpen"
            :class="['input text-sm flex items-center gap-1.5 cursor-pointer select-none min-w-44',
              (filters.node_ids.length || filters.tree_ids.length) ? 'border-blue-500 bg-blue-500/5' : '']">
            <svg class="w-3.5 h-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7h18M3 12h12M3 17h8"/>
            </svg>
            <span v-if="!filters.node_ids.length && !filters.tree_ids.length" class="text-slate-400 flex-1 text-left">Hierarchieknoten …</span>
            <span v-else class="text-blue-600 dark:text-blue-400 font-medium flex-1 text-left text-xs truncate">
              {{ hierarchyFilterLabel }}
            </span>
            <svg class="w-3 h-3 text-slate-400 shrink-0 transition-transform" :class="nodeDropOpen ? 'rotate-180' : ''"
              fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>

          <!-- Dropdown -->
          <div v-if="nodeDropOpen"
            class="absolute z-20 left-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl overflow-hidden"
            style="min-width: 300px; max-width: 400px">

            <!-- Search input -->
            <div class="p-2 border-b border-slate-100 dark:border-slate-700">
              <input ref="nodeSearchInput"
                v-model="nodeSearchQ"
                @input="onNodeSearch"
                @click.stop
                type="text"
                class="w-full input text-sm py-1.5"
                placeholder="Knoten suchen …"
              />
            </div>

            <!-- Currently selected trees + nodes (shown when no search text) -->
            <div v-if="(filters.tree_ids.length || filters.node_ids.length) && !nodeSearchQ"
              class="border-b border-slate-100 dark:border-slate-700">
              <div class="px-3 py-1 text-xs text-slate-400 font-medium uppercase tracking-wide">Ausgewählt</div>
              <!-- Selected trees -->
              <button v-for="t in filters.tree_ids" :key="t.tree_id"
                @click="toggleTreeFilter(t)"
                class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                <span class="flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center bg-blue-500 border-blue-500">
                  <svg class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
                  </svg>
                </span>
                <svg class="w-3 h-3 text-blue-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7h18M3 12h12M3 17h8"/>
                </svg>
                <span class="text-blue-600 dark:text-blue-400 font-medium truncate">{{ t.tree_name }}</span>
                <span class="text-xs text-slate-400 ml-auto shrink-0">Ganzer Ast</span>
              </button>
              <!-- Selected nodes -->
              <button v-for="n in filters.node_ids" :key="n.node_id"
                @click="toggleNode(n)"
                class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                <span class="flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center bg-blue-500 border-blue-500">
                  <svg class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
                  </svg>
                </span>
                <span class="text-xs text-slate-400 shrink-0">{{ n.tree_name }}</span>
                <svg class="w-3 h-3 text-slate-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                </svg>
                <span class="text-blue-600 dark:text-blue-400 font-medium truncate">{{ n.node_name }}</span>
              </button>
            </div>

            <!-- Search results -->
            <div class="max-h-52 overflow-y-auto">
              <div v-if="nodeSearchLoading" class="flex justify-center py-3"><Spinner size="sm" /></div>
              <div v-else-if="nodeResults.length === 0 && nodeSearchQ" class="text-xs text-slate-500 text-center py-3">Keine Treffer</div>
              <div v-else-if="nodeResults.length === 0 && !nodeSearchQ" class="text-xs text-slate-500 text-center py-3">Tippe zum Suchen …</div>
              <button v-else v-for="node in nodeResults" :key="node.node_id"
                @click="toggleNode(node)"
                class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                <span :class="['flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-colors',
                  isNodeSelected(node.node_id) ? 'bg-blue-500 border-blue-500' : 'border-slate-300 dark:border-slate-600']">
                  <svg v-if="isNodeSelected(node.node_id)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
                  </svg>
                </span>
                <span class="text-xs text-slate-400 shrink-0">{{ node.tree_name }}</span>
                <svg class="w-3 h-3 text-slate-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                </svg>
                <span :class="isNodeSelected(node.node_id) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-slate-700 dark:text-slate-200'"
                  class="truncate">{{ node.node_name }}</span>
              </button>
            </div>

            <div v-if="filters.node_ids.length || filters.tree_ids.length" class="border-t border-slate-100 dark:border-slate-700 p-1.5">
              <button @click="clearHierarchyFilters"
                class="w-full text-xs text-center text-slate-500 hover:text-red-500 transition-colors py-1">
                Auswahl aufheben
              </button>
            </div>
          </div>
        </div>

        <!-- Aktive Filter: Alle zurücksetzen -->
        <button v-if="hasActiveFilters" @click="clearAllFilters"
          class="text-xs text-slate-500 hover:text-red-500 dark:hover:text-red-400 transition-colors ml-auto flex items-center gap-1"
          data-testid="btn-clear-filters">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
          Alle Filter zurücksetzen
        </button>
      </div>
    </div>

    <!-- Tabelle -->
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
              <th class="w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="dp in store.items" :key="dp.id" :data-testid="'dp-row-' + dp.id">

              <!-- Name + Hierarchie -->
              <td>
                <RouterLink :to="`/datapoints/${dp.id}`" class="font-medium hover:text-blue-400 transition-colors block leading-snug">
                  {{ dp.name }}
                </RouterLink>
                <div v-if="dp.hierarchy_nodes?.length" class="flex flex-wrap gap-1 mt-1">
                  <span
                    v-for="ref in dp.hierarchy_nodes"
                    :key="ref.node_id"
                    :class="['inline-flex items-center gap-0.5 rounded text-xs transition-colors',
                      isNodeSelected(ref.node_id) || isAncestorSelected(ref)
                        ? 'bg-blue-500/20 text-blue-600 dark:text-blue-300'
                        : 'bg-slate-100 dark:bg-slate-700/60 text-slate-500']"
                    :title="hierarchyFullPath(ref)">
                    <!-- Anzeige-Start: Tree-Name oder konfigurierbarer Ancestor -->
                    <span
                      v-if="hierarchyDisplayAncestor(ref)"
                      @click.prevent="toggleNode({ node_id: hierarchyDisplayAncestor(ref).node_id, node_name: hierarchyDisplayAncestor(ref).node_name, tree_name: ref.tree_name })"
                      :class="['opacity-70 px-1.5 py-0.5 rounded-l cursor-pointer hover:bg-blue-500/20 hover:opacity-100 transition-colors',
                        isNodeSelected(hierarchyDisplayAncestor(ref).node_id) ? 'opacity-100' : '']">
                      {{ hierarchyDisplayAncestor(ref).node_name }}
                    </span>
                    <span
                      v-else
                      @click.prevent="toggleTreeFilter({ tree_id: ref.tree_id, tree_name: ref.tree_name })"
                      :class="['opacity-70 px-1.5 py-0.5 rounded-l cursor-pointer hover:bg-blue-500/20 hover:opacity-100 transition-colors',
                        isTreeSelected(ref.tree_id) ? 'opacity-100' : '']">
                      {{ ref.tree_name }}
                    </span>
                    <svg class="w-2.5 h-2.5 opacity-50 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                    <!-- Blatt-Knoten -->
                    <span
                      @click.prevent="toggleNode({ node_id: ref.node_id, node_name: ref.node_name, tree_name: ref.tree_name })"
                      :class="['px-1.5 py-0.5 rounded-r cursor-pointer hover:bg-blue-500/20 transition-colors',
                        isNodeSelected(ref.node_id) ? 'font-medium' : '']">
                      {{ ref.node_name }}
                    </span>
                  </span>
                </div>
              </td>

              <td><Badge variant="info" size="xs">{{ dp.data_type }}</Badge></td>

              <td>
                <div class="flex flex-wrap gap-1">
                  <Badge v-for="t in dp.tags" :key="t" variant="default" size="xs"
                    class="cursor-pointer hover:opacity-70" @click="setTagFilter(t)">{{ t }}</Badge>
                </div>
              </td>

              <td>
                <RouterLink :to="`/datapoints/${dp.id}`"
                  class="font-mono text-sm text-blue-500 dark:text-blue-300 hover:text-blue-400 transition-colors">
                  {{ liveValue(dp) }}
                </RouterLink>
              </td>

              <td>
                <Badge :variant="qualityVariant(liveQuality(dp))" dot size="xs">
                  {{ qualityLabel(liveQuality(dp)) ?? '—' }}
                </Badge>
              </td>

              <td>
                <div class="flex items-center gap-1">
                  <RouterLink :to="`/datapoints/${dp.id}`" class="btn-icon" title="Details">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.5 6.5l4-4a4.95 4.95 0 017 7l-4 4m-10 4l-4 4a4.95 4.95 0 01-7-7l4-4m5.5 3.5l5-5"/>
                    </svg>
                  </RouterLink>
                  <button @click="openEdit(dp)" class="btn-icon" title="Bearbeiten">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                    </svg>
                  </button>
                  <button @click="confirmDelete(dp)" class="btn-icon text-red-400" title="Löschen">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                    </svg>
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

    <!-- Loading more -->
    <div v-if="store.loading && store.items.length" class="flex justify-center py-4">
      <Spinner size="sm" />
    </div>

    <!-- End of list -->
    <div v-if="!store.hasMore && store.items.length > 0 && !store.loading"
      class="text-center text-slate-400 text-xs py-2">
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
import { hierarchyApi } from '@/api/client'
import Badge         from '@/components/ui/Badge.vue'
import Spinner       from '@/components/ui/Spinner.vue'
import Modal         from '@/components/ui/Modal.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import DataPointForm from '@/components/datapoints/DataPointForm.vue'

// Inline sort-indicator
const SortIcon = {
  props: ['col', 'active', 'dir'],
  template: `<span class="inline-block ml-0.5 opacity-40" :class="{ 'opacity-100 text-blue-500': active === col }">
    <svg v-if="active !== col" class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4"/></svg>
    <svg v-else-if="dir === 'asc'" class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
    <svg v-else                    class="inline w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
  </span>`,
}

const qualityOptions = [
  { value: 'good',      label: 'Gut',       dot: 'bg-green-500',  activeClass: 'border-green-500 bg-green-500/10 text-green-600 dark:text-green-400' },
  { value: 'uncertain', label: 'Unbekannt', dot: 'bg-amber-400',  activeClass: 'border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  { value: 'bad',       label: 'Schlecht',  dot: 'bg-red-500',    activeClass: 'border-red-500 bg-red-500/10 text-red-600 dark:text-red-400' },
]

const store = useDatapointStore()
const ws    = useWebSocketStore()

const filters      = ref({ q: '', tags: [], quality: '', type: '', node_ids: [], tree_ids: [] })
const showForm     = ref(false)
const showConfirm  = ref(false)
const editTarget   = ref(null)
const deleteTarget = ref(null)
const sentinelEl   = ref(null)
const nodeFilterRef = ref(null)
const tagFilterRef  = ref(null)
const tagDropOpen   = ref(false)

// Node filter state
const nodeSearchQ      = ref('')
const nodeResults      = ref([])
const nodeSearchLoading = ref(false)
const nodeDropOpen     = ref(false)
let nodeSearchTimer    = null

let searchTimeout = null
let observer      = null
let unsubWs       = null

const hasActiveFilters = computed(() =>
  !!(filters.value.q || filters.value.tags.length || filters.value.quality || filters.value.type || filters.value.node_ids.length || filters.value.tree_ids.length)
)

const hierarchyFilterLabel = computed(() => {
  const trees = filters.value.tree_ids
  const nodes = filters.value.node_ids
  const total = trees.length + nodes.length
  if (total === 0) return ''
  if (total === 1) {
    if (trees.length === 1) return trees[0].tree_name
    return `${nodes[0].tree_name} › ${nodes[0].node_name}`
  }
  return `${total} Filter`
})

// --------------------------------------------------------------------------
// Lifecycle
// --------------------------------------------------------------------------

onMounted(async () => {
  await Promise.all([store.loadDatatypes(), store.loadTags()])

  const saved = store.restoreScrollState()
  if (saved) {
    Object.assign(filters.value, {
      ...saved.filters,
      tags:     saved.filters?.tags     ?? [],
      node_ids: saved.filters?.node_ids ?? [],
      tree_ids: saved.filters?.tree_ids ?? [],
    })
    store.clearScrollState()
    await store.search(apiFilters(), false)
    let pages = 0
    while (store.items.length < saved.count && store.hasMore && pages < 4) {
      await store.loadMore()
      pages++
    }
    await nextTick()
    window.scrollTo({ top: saved.scrollY, behavior: 'instant' })
  } else {
    await store.search(apiFilters(), false)
  }

  unsubWs = ws.onValue((id, value, quality) => store.patchValue(id, value, quality))
  _setupObserver()

  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  unsubWs?.()
  observer?.disconnect()
  document.removeEventListener('click', onDocClick)
})

onBeforeRouteLeave((to) => {
  if (to.name === 'DataPointDetail') {
    store.saveScrollState(window.scrollY, { ...filters.value })
  }
})

watch(() => store.items, (items) => {
  ws.subscribe(items.map(d => d.id))
}, { immediate: true })

// --------------------------------------------------------------------------
// Infinite scroll
// --------------------------------------------------------------------------

function _makeObserver() {
  const root = document.querySelector('main') ?? null
  return new IntersectionObserver(
    ([entry]) => { if (entry.isIntersecting && store.hasMore && !store.loading) store.loadMore() },
    { root, rootMargin: '300px' }
  )
}
function _setupObserver() {
  if (!sentinelEl.value) return
  observer = _makeObserver()
  observer.observe(sentinelEl.value)
}
watch(sentinelEl, (el) => {
  observer?.disconnect()
  if (el) { observer = _makeObserver(); observer.observe(el) }
})

// --------------------------------------------------------------------------
// Filter helpers
// --------------------------------------------------------------------------

function apiFilters() {
  return {
    q:       filters.value.q,
    tag:     filters.value.tags.join(','),
    quality: filters.value.quality,
    type:    filters.value.type,
    node_id: filters.value.node_ids.map(n => n.node_id).join(','),
    tree_id: filters.value.tree_ids.map(t => t.tree_id).join(','),
  }
}

function onSearch() {
  clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => store.search(apiFilters(), false), 350)
}

function toggleQuality(val) {
  filters.value.quality = filters.value.quality === val ? '' : val
  onSearch()
}

function toggleTag(tag) {
  const idx = filters.value.tags.indexOf(tag)
  if (idx === -1) filters.value.tags.push(tag)
  else filters.value.tags.splice(idx, 1)
  onSearch()
}

function setTagFilter(tag) {
  if (!filters.value.tags.includes(tag)) {
    filters.value.tags.push(tag)
    onSearch()
  }
}

function clearFilter(key) {
  if (key === 'node_ids' || key === 'node_id') {
    filters.value.node_ids = []
    nodeSearchQ.value = ''
    nodeResults.value = []
  } else if (key === 'tree_ids') {
    filters.value.tree_ids = []
  } else if (key === 'tags') {
    filters.value.tags = []
  } else {
    filters.value[key] = ''
  }
  onSearch()
}

function clearAllFilters() {
  filters.value = { q: '', tags: [], quality: '', type: '', node_ids: [], tree_ids: [] }
  nodeSearchQ.value = ''
  nodeResults.value = []
  onSearch()
}

// --------------------------------------------------------------------------
// Hierarchy (tree + node) filter helpers
// --------------------------------------------------------------------------

function clearHierarchyFilters() {
  filters.value.node_ids = []
  filters.value.tree_ids = []
  nodeSearchQ.value = ''
  nodeResults.value = []
  onSearch()
}

// --------------------------------------------------------------------------
// Tree filter
// --------------------------------------------------------------------------

function isTreeSelected(tree_id) {
  return filters.value.tree_ids.some(t => t.tree_id === tree_id)
}

function toggleTreeFilter(tree) {
  const idx = filters.value.tree_ids.findIndex(t => t.tree_id === tree.tree_id)
  if (idx === -1) filters.value.tree_ids.push({ tree_id: tree.tree_id, tree_name: tree.tree_name })
  else filters.value.tree_ids.splice(idx, 1)
  onSearch()
}

// --------------------------------------------------------------------------
// Node multi-select
// --------------------------------------------------------------------------

function isNodeSelected(node_id) {
  return filters.value.node_ids.some(n => n.node_id === node_id)
}

function toggleNode(node) {
  const idx = filters.value.node_ids.findIndex(n => n.node_id === node.node_id)
  if (idx === -1) {
    filters.value.node_ids.push({ node_id: node.node_id, node_name: node.node_name, tree_name: node.tree_name })
  } else {
    filters.value.node_ids.splice(idx, 1)
  }
  onSearch()
}

function onNodeSearch() {
  clearTimeout(nodeSearchTimer)
  nodeSearchTimer = setTimeout(async () => {
    if (!nodeSearchQ.value.trim()) { nodeResults.value = []; return }
    nodeSearchLoading.value = true
    try {
      const { data } = await hierarchyApi.searchNodes(nodeSearchQ.value.trim(), 30)
      nodeResults.value = data
    } finally {
      nodeSearchLoading.value = false
    }
  }, 220)
}

function onDocClick(e) {
  if (nodeFilterRef.value && !nodeFilterRef.value.contains(e.target)) nodeDropOpen.value = false
  if (tagFilterRef.value && !tagFilterRef.value.contains(e.target)) tagDropOpen.value = false
}

// --------------------------------------------------------------------------
// CRUD
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
// Hierarchy path helpers
// --------------------------------------------------------------------------

function hierarchyFullPath(ref) {
  const parts = [ref.tree_name, ...(ref.node_path || []).map(n => n.node_name), ref.node_name]
  return parts.join(' › ')
}

function hierarchyDisplayAncestor(ref) {
  if (!ref.display_depth || ref.display_depth === 0) return null
  const idx = ref.display_depth - 1
  return ref.node_path?.[idx] ?? null
}

function isAncestorSelected(ref) {
  const ancestor = hierarchyDisplayAncestor(ref)
  return ancestor ? isNodeSelected(ancestor.node_id) : false
}

// --------------------------------------------------------------------------
// Live value helpers
// --------------------------------------------------------------------------

function liveValue(dp) {
  const v = ws.liveValues[dp.id]?.value ?? dp.value
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
