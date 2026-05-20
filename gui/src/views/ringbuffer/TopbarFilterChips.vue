<template>
  <div class="flex flex-wrap items-center gap-2" data-testid="topbar-filter-chips">
    <!-- Slot for the time filter component (#432 integrates here) -->
    <div v-if="$slots['time-filter-slot']" class="shrink-0">
      <slot name="time-filter-slot" />
    </div>

    <!-- Active topbar chips with drag-reorder -->
    <VueDraggable
      v-model="activeSets"
      class="flex flex-wrap items-center gap-2"
      :animation="150"
      handle=".chip-drag-handle"
      @end="onDragEnd"
    >
      <div
        v-for="set in activeSets"
        :key="set.id"
        :data-testid="`topbar-chip-${set.id}`"
        class="chip-drag-handle group inline-flex items-center rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-sm overflow-hidden"
      >
        <!-- Left color bar -->
        <span
          :data-testid="`topbar-chip-color-${set.id}`"
          class="w-1.5 self-stretch shrink-0"
          :style="{ backgroundColor: set.color || '#94a3b8' }"
        />
        <!-- Active/inactive toggle (per-user — #478, open to everyone) -->
        <button
          type="button"
          :data-testid="`topbar-chip-toggle-${set.id}`"
          class="px-2 py-1 text-sm text-slate-600 dark:text-slate-300 hover:text-slate-800 dark:hover:text-white"
          :title="set.is_active ? 'Aktiv — klicken zum Deaktivieren (nur für mich)' : 'Inaktiv — klicken zum Aktivieren (nur für mich)'"
          @click.stop="onToggleActive(set)"
        >
          {{ set.is_active ? '●' : '○' }}
        </button>
        <!-- Chip body (edit) -->
        <button
          type="button"
          :data-testid="`topbar-chip-body-${set.id}`"
          class="px-2 py-1 text-sm text-slate-800 dark:text-slate-100 hover:underline focus:outline-none"
          :title="ownerTitle(set)"
          @click.stop="$emit('edit-set', set.id)"
        >
          <span
            v-if="isEmptyFilter(set.filter)"
            :data-testid="`topbar-chip-empty-${set.id}`"
            class="mr-1 text-amber-500"
            title="Dieses Set hat keinen Filter konfiguriert — die Tabelle bleibt leer, solange das Set aktiv ist."
          >⚠</span>
          {{ set.name }}
          <!-- Owner hint: visible for everyone (including admin) on every set
               the caller does NOT own. Shared legacy sets (created_by==null)
               show "shared". The lock icon is only added when the caller has
               no write access (non-admin, non-owner) so admin sees the owner
               without misleading "read-only" affordance. -->
          <span
            v-if="!isMine(set)"
            :data-testid="`topbar-chip-owner-${set.id}`"
            class="ml-1 text-xs text-slate-400 dark:text-slate-500"
            :title="ownerTitle(set)"
          >
            <span v-if="set.created_by">@{{ set.created_by }}</span>
            <span v-else class="italic">geteilt</span>
            <span
              v-if="!canEdit(set)"
              :data-testid="`topbar-chip-owner-lock-${set.id}`"
              class="ml-0.5"
            >🔒</span>
          </span>
        </button>
        <!-- Remove from topbar -->
        <button
          type="button"
          :data-testid="`topbar-chip-remove-${set.id}`"
          class="px-2 py-1 text-xs text-slate-400 hover:text-red-500 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
          title="Aus Topbar entfernen"
          @click.stop="onRemoveFromTopbar(set)"
        >
          ×
        </button>
      </div>
    </VueDraggable>

    <!-- Export trigger -->
    <button
      type="button"
      data-testid="topbar-export-btn"
      class="btn-secondary btn-sm"
      @click="$emit('export')"
    >
      ↓ Export
    </button>

    <!-- + Filter dropdown -->
    <div class="relative">
      <button
        type="button"
        data-testid="topbar-add-filter-btn"
        class="btn-secondary btn-sm"
        @click="toggleAddMenu"
      >
        + Filter ▾
      </button>
      <div
        v-if="addMenuOpen"
        data-testid="topbar-add-filter-menu"
        class="absolute right-0 mt-1 w-72 z-40 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg overflow-hidden"
        @click.stop
      >
        <!-- pinned "+ Neu" as the first option (#36 UX) -->
        <button
          type="button"
          data-testid="topbar-add-filter-new"
          class="block w-full text-left px-3 py-2 text-sm font-medium text-blue-600 dark:text-blue-400 hover:bg-slate-100 dark:hover:bg-slate-800 border-b border-slate-200 dark:border-slate-700"
          @click="onCreateNew"
        >
          + Neu …
        </button>

        <!-- Search input -->
        <input
          ref="searchInputRef"
          v-model="addMenuQuery"
          type="search"
          data-testid="topbar-add-filter-search"
          placeholder="Filter suchen …"
          class="block w-full px-3 py-2 text-sm bg-transparent border-b border-slate-200 dark:border-slate-700 outline-none focus:border-blue-500"
        />

        <!-- Filtered list -->
        <div class="max-h-64 overflow-y-auto">
          <button
            v-for="set in filteredAvailableSets"
            :key="set.id"
            type="button"
            :data-testid="`topbar-add-filter-item-${set.id}`"
            class="block w-full text-left px-3 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
            @click="onAddToTopbar(set)"
          >
            <span
              class="inline-block w-2 h-2 rounded-full mr-2 align-middle"
              :style="{ backgroundColor: set.color || '#94a3b8' }"
            />
            {{ set.name }}
          </button>
          <div
            v-if="!filteredAvailableSets.length"
            data-testid="topbar-add-filter-empty"
            class="px-3 py-2 text-xs text-slate-500"
          >
            {{ addMenuQuery ? 'Keine Treffer' : 'Keine weiteren Sets verfügbar' }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { VueDraggable } from 'vue-draggable-plus'
import { ringbufferApi } from '@/api/client'
import { isEmptyFilter } from '@/composables/useClientSideMatch'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits(['edit-set', 'new-set', 'changed', 'export'])

const auth = useAuthStore()
const filtersets = ref([])
const addMenuOpen = ref(false)
const addMenuQuery = ref('')
const searchInputRef = ref(null)

// Admin can edit every set; non-admin users only the sets they created
// themselves (#478). All per-user state (is_active toggle, topbar pinning,
// drag-reorder, +Add, ×Remove) stays open for everyone — only the chip body
// (which jumps into the FilterEditor for editing/deleting) signals the lock
// via the 🔒 marker.
function canEdit(set) {
  if (!set) return false
  if (auth.isAdmin) return true
  const owner = set.created_by
  return owner != null && owner === auth.username
}

function isMine(set) {
  return !!set && set.created_by != null && set.created_by === auth.username
}

function ownerTitle(set) {
  if (!set) return ''
  if (isMine(set)) return 'Eigenes Set — bearbeiten'
  if (set.created_by == null) return 'Geteiltes Set (nur Admin darf bearbeiten)'
  if (canEdit(set)) return `Eigentümer: ${set.created_by} — bearbeiten als Admin`
  return `Eigentümer: ${set.created_by} — klicken um zu öffnen / zu klonen`
}

const activeSets = computed({
  get() {
    return filtersets.value
      .filter((s) => s.topbar_active)
      .slice()
      .sort((a, b) => (a.topbar_order ?? 0) - (b.topbar_order ?? 0))
  },
  set(newList) {
    // Update topbar_order in the underlying store so the UI stays in sync.
    const idMap = new Map(newList.map((s, idx) => [s.id, idx]))
    for (const set of filtersets.value) {
      if (idMap.has(set.id)) set.topbar_order = idMap.get(set.id)
    }
  },
})

const availableSets = computed(() =>
  filtersets.value.filter((s) => !s.topbar_active),
)

const filteredAvailableSets = computed(() => {
  const q = addMenuQuery.value.trim().toLowerCase()
  if (!q) return availableSets.value
  return availableSets.value.filter((s) =>
    (s.name || '').toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q),
  )
})

async function load() {
  try {
    const { data } = await ringbufferApi.listFiltersets()
    filtersets.value = Array.isArray(data) ? data : []
  } catch {
    filtersets.value = []
  }
}

async function onToggleActive(set) {
  const next = !set.is_active
  set.is_active = next
  try {
    await ringbufferApi.patchFiltersetTopbar(set.id, { is_active: next })
    emit('changed')
  } catch {
    // Roll back optimistic update on failure
    set.is_active = !next
  }
}

async function onRemoveFromTopbar(set) {
  set.topbar_active = false
  try {
    await ringbufferApi.patchFiltersetTopbar(set.id, { topbar_active: false })
    emit('changed')
  } catch {
    set.topbar_active = true
  }
}

async function onAddToTopbar(set) {
  set.topbar_active = true
  addMenuOpen.value = false
  try {
    await ringbufferApi.patchFiltersetTopbar(set.id, { topbar_active: true })
    emit('changed')
  } catch {
    set.topbar_active = false
  }
}

async function onDragEnd() {
  // The PATCH /filtersets/order endpoint expects [{id, topbar_order}, ...]
  // — passing a plain string array yielded a 422 that the catch swallowed,
  // and the snap-back to the original order was the inevitable consequence
  // of the subsequent server reload.
  const items = activeSets.value.map((s, idx) => ({ id: s.id, topbar_order: idx }))
  try {
    await ringbufferApi.patchFiltersetOrder(items)
    emit('changed')
  } catch {
    // Best-effort: reload the truth from the server on failure
    await load()
  }
}

function toggleAddMenu() {
  addMenuOpen.value = !addMenuOpen.value
  if (addMenuOpen.value) {
    addMenuQuery.value = ''
    nextTick(() => searchInputRef.value?.focus())
  }
}

function onCreateNew() {
  addMenuOpen.value = false
  addMenuQuery.value = ''
  emit('new-set')
}

function onDocumentClick(event) {
  if (!addMenuOpen.value) return
  const menu = document.querySelector('[data-testid="topbar-add-filter-menu"]')
  const btn = document.querySelector('[data-testid="topbar-add-filter-btn"]')
  if (menu?.contains(event.target) || btn?.contains(event.target)) return
  addMenuOpen.value = false
  addMenuQuery.value = ''
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
  void load()
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentClick)
})

defineExpose({ reload: load })
</script>
