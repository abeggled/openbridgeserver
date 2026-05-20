<template>
  <Modal
    :model-value="modelValue"
    :soft-backdrop="softModal"
    :title="setId ? 'Filterset bearbeiten' : 'Neues Filterset'"
    max-width="2xl"
    @update:model-value="onModalToggle"
  >
    <div class="flex flex-col gap-5">
      <!-- Owner / permission banner (#478): visible on every existing set -->
      <p
        v-if="setId && loadedSet"
        data-testid="filter-editor-owner-line"
        class="text-xs text-slate-500 dark:text-slate-400"
      >
        <span v-if="loadedSet.created_by === auth.username">Eigenes Set</span>
        <span v-else-if="loadedSet.created_by">Eigentümer: <strong>{{ loadedSet.created_by }}</strong></span>
        <span v-else>Geteiltes Set (vor #478 angelegt — nur Admin darf bearbeiten)</span>
        <span v-if="!canEdit" class="ml-2 inline-flex items-center rounded bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">Nur lesend</span>
      </p>

      <!-- Set-Metadaten -->
      <section class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Name</label>
          <input
            v-model="form.name"
            class="input"
            data-testid="filter-editor-name"
            placeholder="z. B. Heizung"
            @input="markDirty"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Beschreibung</label>
          <input
            v-model="form.description"
            class="input"
            data-testid="filter-editor-description"
            placeholder="Optionale Beschreibung"
            @input="markDirty"
          />
        </div>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">Farbe</label>
        <div class="flex flex-wrap items-center gap-1.5" data-testid="filter-editor-color-palette">
          <button
            v-for="color in COLOR_PALETTE"
            :key="color"
            type="button"
            :data-testid="`filter-editor-color-${color}`"
            :data-color="color"
            class="w-6 h-6 rounded-full border-2 transition"
            :class="form.color === color ? 'border-slate-900 dark:border-white' : 'border-transparent hover:border-slate-400'"
            :style="{ backgroundColor: color }"
            :title="color"
            @click="onPickColor(color)"
          />
        </div>
      </section>

      <!-- Hierarchy combobox with per-chip ⊞ expand -->
      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">Hierarchy-Knoten</label>
        <HierarchyCombobox
          :model-value="hierarchyIds"
          data-testid="filter-editor-hierarchy"
          @update:model-value="onHierarchyChange"
        >
          <!-- The remove (×) action is rendered by the surrounding Combobox
               wrapper — we only inject content + the ⊞ expand affordance. -->
          <template #chip="{ item, index }">
            <span class="truncate">{{ chipLabel(item) }}</span>
            <button
              type="button"
              :data-testid="`hierarchy-expand-${index}`"
              class="ml-1 text-blue-700/80 hover:text-emerald-600 dark:text-blue-300/80 dark:hover:text-emerald-300"
              title="Knoten auflösen: DPs als Chips materialisieren"
              :disabled="expanding"
              @click.stop="expandHierarchyChip(item, index)"
            >
              ⊞
            </button>
          </template>
        </HierarchyCombobox>
        <p class="text-xs text-slate-500">Live-Filter — auch zukünftige Objekte unter diesen Knoten.</p>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">Datenpunkte</label>
        <DpCombobox
          :multi="true"
          :model-value="form.datapoints"
          data-testid="filter-editor-dps"
          @update:model-value="onDpsChange"
        />
        <p class="text-xs text-slate-500">Feste Auswahl.</p>
      </section>

      <section class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Tags</label>
          <TagCombobox
            :model-value="form.tags"
            data-testid="filter-editor-tags"
            @update:model-value="onTagsChange"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Adapter</label>
          <AdapterCombobox
            :model-value="form.adapters"
            data-testid="filter-editor-adapters"
            @update:model-value="onAdaptersChange"
          />
        </div>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">Volltextsuche (q)</label>
        <input
          v-model="form.q"
          class="input"
          data-testid="filter-editor-q"
          placeholder="z. B. Temperatur"
          @input="markDirty"
        />
      </section>

      <!-- Wertfilter -->
      <section class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2">
        <h4 class="text-sm font-semibold">Wertfilter</h4>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-2">
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">Datentyp</label>
            <select
              v-model="form.valueDataType"
              class="input"
              data-testid="filter-editor-value-type"
              @change="onValueTypeChange"
            >
              <option value="number">Nummer</option>
              <option value="string">String</option>
              <option value="bool">Bool</option>
              <option value="regex">Regex</option>
            </select>
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">Operator</label>
            <select
              v-model="form.valueOperator"
              class="input"
              data-testid="filter-editor-value-operator"
              @change="markDirty"
            >
              <option value="">(kein)</option>
              <option v-for="op in operatorsFor(form.valueDataType)" :key="op" :value="op">{{ op }}</option>
            </select>
          </div>
          <template v-if="form.valueOperator === 'between'">
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-500">Untergrenze</label>
              <input
                v-model="form.valueLower"
                class="input"
                data-testid="filter-editor-value-lower"
                placeholder="0"
                @input="markDirty"
              />
            </div>
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-500">Obergrenze</label>
              <input
                v-model="form.valueUpper"
                class="input"
                data-testid="filter-editor-value-upper"
                placeholder="100"
                @input="markDirty"
              />
            </div>
          </template>
          <template v-else-if="form.valueOperator === 'regex' || form.valueDataType === 'regex'">
            <div class="flex flex-col gap-1 md:col-span-2">
              <label class="text-xs text-slate-500">Pattern</label>
              <input
                v-model="form.valuePattern"
                class="input"
                data-testid="filter-editor-value-pattern"
                placeholder="^temp"
                @input="markDirty"
              />
            </div>
          </template>
          <template v-else-if="form.valueOperator">
            <div class="flex flex-col gap-1 md:col-span-2">
              <label class="text-xs text-slate-500">Wert</label>
              <input
                v-model="form.valueInput"
                class="input"
                data-testid="filter-editor-value-input"
                placeholder="42"
                @input="markDirty"
              />
            </div>
          </template>
        </div>
        <label
          v-if="form.valueOperator === 'regex' || form.valueDataType === 'regex'"
          class="inline-flex items-center gap-2 text-xs text-slate-500"
        >
          <input
            type="checkbox"
            v-model="form.valueIgnoreCase"
            data-testid="filter-editor-value-ignore-case"
            @change="markDirty"
          />
          Regex ignore case
        </label>
      </section>

      <p v-if="errorMsg" data-testid="filter-editor-error" class="text-sm text-red-500">{{ errorMsg }}</p>
    </div>

    <template #footer>
      <p class="text-xs mr-auto self-center" data-testid="filter-editor-semantics-hint"
         :class="filterIsEmpty ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500'">
        <span v-if="filterIsEmpty" data-testid="filter-editor-empty-hint">
          ⚠ Mindestens ein Filterkriterium konfigurieren — sonst kann das Set nicht gespeichert werden.
        </span>
        <span v-else>
          Innerhalb des Sets: Hierarchy OR DP, alle anderen Kriterien AND-verknüpft.
        </span>
      </p>
      <button
        v-if="setId"
        class="btn-danger btn-sm"
        :disabled="deleting || saving || !canEdit"
        data-testid="filter-editor-delete"
        :title="canEdit ? 'Filter-Set unwiderruflich löschen' : 'Nur der Eigentümer oder ein Admin darf löschen'"
        @click="onDelete"
      >
        🗑 Löschen
      </button>
      <button class="btn-secondary btn-sm" data-testid="filter-editor-cancel" @click="onCancel">Verwerfen</button>
      <button
        class="btn-primary btn-sm"
        :disabled="saving || filterIsEmpty || deleting || !canEdit"
        data-testid="filter-editor-save-topbar"
        :title="canEdit ? '' : 'Nur der Eigentümer oder ein Admin darf speichern'"
        @click="onSave(true)"
      >
        Speichern &amp; in Topleiste
      </button>
    </template>
  </Modal>

  <ConfirmDialog
    v-model="confirmOpen"
    title="Ungespeicherte Änderungen"
    message="Editor wirklich schliessen und alle Änderungen verwerfen?"
    confirm-label="Verwerfen"
    @confirm="confirmDiscard"
  />
  <ConfirmDialog
    v-model="confirmDeleteOpen"
    title="Filter-Set löschen"
    :message="`Das Filter-Set „${loadedSet?.name ?? ''}“ wirklich unwiderruflich löschen?`"
    confirm-label="Löschen"
    :danger="true"
    @confirm="confirmDelete"
  />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ringbufferApi, searchApi, hierarchyApi } from '@/api/client'
import Modal from '@/components/ui/Modal.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import HierarchyCombobox from '@/components/ui/HierarchyCombobox.vue'
import DpCombobox from '@/components/ui/DpCombobox.vue'
import TagCombobox from '@/components/ui/TagCombobox.vue'
import AdapterCombobox from '@/components/ui/AdapterCombobox.vue'
import { isEmptyFilter } from '@/composables/useClientSideMatch'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  setId: { type: String, default: null },
  softModal: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'saved', 'deleted'])

const COLOR_PALETTE = [
  // Vibrant tier
  '#3b82f6', // blue
  '#10b981', // emerald
  '#84cc16', // lime
  '#f59e0b', // amber
  '#ef4444', // red
  '#ec4899', // pink
  '#8b5cf6', // violet
  '#14b8a6', // teal
  // Dark / muted tier
  '#1e3a8a', // navy
  '#064e3b', // forest
  '#7c2d12', // rust
  '#7f1d1d', // burgundy
  '#581c87', // deep purple
  '#0f172a', // near-black
  '#94a3b8', // slate
]
const DEFAULT_COLOR = COLOR_PALETTE[0]

const OPERATOR_OPTIONS = {
  number: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between'],
  string: ['eq', 'ne', 'contains', 'regex'],
  bool: ['eq', 'ne'],
  regex: ['regex'],
}

function makeEmptyForm() {
  return {
    name: '',
    description: '',
    color: DEFAULT_COLOR,
    hierarchy_nodes: [], // {tree_id, node_id, include_descendants}
    datapoints: [],
    tags: [],
    adapters: [],
    q: '',
    valueDataType: 'number',
    valueOperator: '',
    valueInput: '',
    valueLower: '',
    valueUpper: '',
    valuePattern: '',
    valueIgnoreCase: false,
  }
}

const form = reactive(makeEmptyForm())

// Disable Save when the filter has no populated criteria — matches backend
// validation (POST/PUT /filtersets reject empty FilterCriteria with 422).
const filterIsEmpty = computed(() =>
  isEmptyFilter({
    hierarchy_nodes: form.hierarchy_nodes,
    datapoints: form.datapoints,
    tags: form.tags,
    adapters: form.adapters,
    q: form.q,
    value_filter: form.valueOperator ? { operator: form.valueOperator } : null,
  }),
)

const errorMsg = ref('')
const saving = ref(false)
const deleting = ref(false)
const dirty = ref(false)
const confirmOpen = ref(false)
const confirmDeleteOpen = ref(false)
const expanding = ref(false)
const loadedSet = ref(null)

// Fine-grained ownership (#478): admin can edit every set; non-admin users
// only the sets they created themselves. New sets (no setId yet) are always
// editable by the caller. Legacy sets (created_by == null) are admin-only.
const canEdit = computed(() => {
  if (!props.setId) return true
  const owner = loadedSet.value?.created_by
  if (auth.isAdmin) return true
  return owner != null && owner === auth.username
})
// Cache hierarchy node descendants by `${tree_id}:${node_id}` so we can
// resolve descendants client-side without needing a dedicated backend route.
const hierarchyTreeCache = new Map() // tree_id -> array of {id, parent_id}

function operatorsFor(dataType) {
  return OPERATOR_OPTIONS[dataType] || OPERATOR_OPTIONS.number
}

function markDirty() {
  dirty.value = true
}

const hierarchyIds = computed(() =>
  form.hierarchy_nodes.map((n) => `${n.tree_id}:${n.node_id}`),
)

function chipLabel(item) {
  // Hierarchy chip respects `tree.display_depth` (PR #462 / issue #443):
  // depth 0 → tree_name as the abbreviated start; depth ≥ 1 → ancestor at
  // index depth-1 in the within-tree path. The leaf node is always the
  // second segment. Falls back to tree_name when the configured depth runs
  // past the leaf (degenerate but tolerated). Full path is available via the
  // tooltip on the surrounding chip wrapper.
  if (!item) return ''
  const path = Array.isArray(item.path) ? item.path : []
  if (path.length === 0) return item.label ?? item.name ?? String(item.id ?? '')
  const leaf = path[path.length - 1]
  if (path.length === 1) return item.tree_name ? `${item.tree_name} › ${leaf}` : leaf
  const depth = Number(item.display_depth) || 0
  // Ancestor indices that make sense: 0 .. path.length-2. depth=0 maps to
  // tree_name (no ancestor index used).
  let start
  if (depth <= 0 || depth - 1 >= path.length - 1) {
    start = item.tree_name || path[0]
  } else {
    start = path[depth - 1]
  }
  return `${start} › ${leaf}`
}

function parseCompositeId(compositeId) {
  const idx = String(compositeId).indexOf(':')
  if (idx <= 0) return null
  return { tree_id: compositeId.slice(0, idx), node_id: compositeId.slice(idx + 1) }
}

function onHierarchyChange(ids) {
  const next = []
  for (const id of ids || []) {
    const parsed = parseCompositeId(id)
    if (!parsed) continue
    const existing = form.hierarchy_nodes.find(
      (n) => n.tree_id === parsed.tree_id && n.node_id === parsed.node_id,
    )
    next.push(
      existing || {
        tree_id: parsed.tree_id,
        node_id: parsed.node_id,
        include_descendants: true,
      },
    )
  }
  form.hierarchy_nodes = next
  markDirty()
}

function onDpsChange(ids) {
  form.datapoints = Array.isArray(ids) ? [...ids] : []
  markDirty()
}

function onTagsChange(ids) {
  form.tags = Array.isArray(ids) ? [...ids] : []
  markDirty()
}

function onAdaptersChange(ids) {
  form.adapters = Array.isArray(ids) ? [...ids] : []
  markDirty()
}

function onPickColor(color) {
  form.color = color
  markDirty()
}

function onValueTypeChange() {
  // Reset operator when switching type because the operator set changes.
  form.valueOperator = ''
  form.valueInput = ''
  form.valueLower = ''
  form.valueUpper = ''
  form.valuePattern = ''
  markDirty()
}

function parseLiteral(raw, dataType) {
  const text = String(raw ?? '').trim()
  if (!text) return null
  if (dataType === 'number') {
    const n = Number(text)
    return Number.isFinite(n) ? n : null
  }
  if (dataType === 'bool') {
    const lower = text.toLowerCase()
    if (lower === 'true' || lower === '1') return true
    if (lower === 'false' || lower === '0') return false
    return null
  }
  return text
}

function buildValueFilter() {
  if (!form.valueOperator) {
    if (form.valueDataType === 'regex' && form.valuePattern.trim()) {
      return {
        operator: 'regex',
        pattern: form.valuePattern.trim(),
        ignore_case: Boolean(form.valueIgnoreCase),
      }
    }
    return null
  }
  if (form.valueOperator === 'between') {
    const lower = parseLiteral(form.valueLower, form.valueDataType)
    const upper = parseLiteral(form.valueUpper, form.valueDataType)
    if (lower === null && upper === null) return null
    return { operator: 'between', lower, upper }
  }
  if (form.valueOperator === 'regex') {
    const pattern = form.valuePattern.trim()
    if (!pattern) return null
    return { operator: 'regex', pattern, ignore_case: Boolean(form.valueIgnoreCase) }
  }
  const value = parseLiteral(form.valueInput, form.valueDataType)
  if (value === null) return null
  return { operator: form.valueOperator, value }
}

function buildPayload() {
  return {
    name: form.name.trim(),
    description: form.description || '',
    dsl_version: 2,
    // Saved sets are always active — there is no inactive-save path in the editor.
    is_active: true,
    color: form.color || DEFAULT_COLOR,
    topbar_active: loadedSet.value?.topbar_active ?? false,
    topbar_order: loadedSet.value?.topbar_order ?? 0,
    filter: {
      hierarchy_nodes: form.hierarchy_nodes.map((n) => ({
        tree_id: String(n.tree_id),
        node_id: String(n.node_id),
        include_descendants: n.include_descendants !== false,
      })),
      datapoints: [...form.datapoints],
      tags: [...form.tags],
      adapters: [...form.adapters],
      q: form.q ? form.q.trim() : null,
      value_filter: buildValueFilter(),
    },
  }
}

function hydrateForm(payload) {
  loadedSet.value = payload
  Object.assign(form, makeEmptyForm())
  if (!payload) {
    dirty.value = false
    return
  }
  form.name = payload.name || ''
  form.description = payload.description || ''
  form.color = payload.color || DEFAULT_COLOR

  const flt = payload.filter || {}
  form.hierarchy_nodes = Array.isArray(flt.hierarchy_nodes)
    ? flt.hierarchy_nodes.map((n) => ({
        tree_id: String(n.tree_id),
        node_id: String(n.node_id),
        include_descendants: n.include_descendants !== false,
      }))
    : []
  form.datapoints = Array.isArray(flt.datapoints) ? [...flt.datapoints] : []
  form.tags = Array.isArray(flt.tags) ? [...flt.tags] : []
  form.adapters = Array.isArray(flt.adapters) ? [...flt.adapters] : []
  form.q = flt.q || ''

  const vf = flt.value_filter
  if (vf?.operator) {
    if (vf.operator === 'regex') {
      form.valueDataType = 'regex'
      form.valueOperator = 'regex'
      form.valuePattern = String(vf.pattern || '')
      form.valueIgnoreCase = Boolean(vf.ignore_case)
    } else if (vf.operator === 'between') {
      form.valueDataType = 'number'
      form.valueOperator = 'between'
      form.valueLower = vf.lower == null ? '' : String(vf.lower)
      form.valueUpper = vf.upper == null ? '' : String(vf.upper)
    } else {
      const raw = vf.value
      if (typeof raw === 'boolean') form.valueDataType = 'bool'
      else if (typeof raw === 'number') form.valueDataType = 'number'
      else form.valueDataType = 'string'
      form.valueOperator = vf.operator
      form.valueInput = raw == null ? '' : String(raw)
    }
  }

  dirty.value = false
}

async function loadSet(id) {
  errorMsg.value = ''
  try {
    const { data } = await ringbufferApi.getFilterset(id)
    hydrateForm(data)
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || 'Filterset konnte nicht geladen werden'
  }
}

async function loadHierarchyTree(treeId) {
  if (hierarchyTreeCache.has(treeId)) return hierarchyTreeCache.get(treeId)
  try {
    const { data } = await hierarchyApi.getTreeNodes(treeId)
    const nodes = Array.isArray(data) ? data : []
    hierarchyTreeCache.set(treeId, nodes)
    return nodes
  } catch {
    hierarchyTreeCache.set(treeId, [])
    return []
  }
}

function collectDescendants(nodes, rootId) {
  const byParent = new Map()
  for (const n of nodes) {
    const key = n.parent_id == null ? null : String(n.parent_id)
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key).push(String(n.id))
  }
  const out = [String(rootId)]
  const stack = [String(rootId)]
  while (stack.length) {
    const cur = stack.pop()
    const children = byParent.get(cur) || []
    for (const child of children) {
      if (!out.includes(child)) {
        out.push(child)
        stack.push(child)
      }
    }
  }
  return out
}

async function expandHierarchyChip(item, index) {
  if (expanding.value) return
  // The combobox's chip-slot passes us the resolved `item` (composite id +
  // path). Map back to our authoritative state via the index.
  const node = form.hierarchy_nodes[index]
  if (!node) return
  expanding.value = true
  errorMsg.value = ''
  try {
    const nodes = await loadHierarchyTree(node.tree_id)
    const nodeIds = collectDescendants(nodes, node.node_id)
    const { data } = await searchApi.search({
      node_id: nodeIds.join(','),
      size: 500,
    })
    const items = Array.isArray(data?.items) ? data.items : []
    const newDpIds = items.map((it) => String(it.id))
    const merged = Array.from(new Set([...form.datapoints, ...newDpIds]))
    form.datapoints = merged
    // Drop the expanded hierarchy chip.
    form.hierarchy_nodes = form.hierarchy_nodes.filter((n, i) => i !== index)
    markDirty()
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || 'Knoten konnten nicht aufgelöst werden'
  } finally {
    expanding.value = false
  }
  // suppress unused-arg warning while still documenting the slot contract
  void item
}

async function onSave(addToTopbar) {
  errorMsg.value = ''
  if (!form.name.trim()) {
    errorMsg.value = 'Name ist erforderlich'
    return
  }
  saving.value = true
  try {
    const payload = buildPayload()
    let savedId
    if (props.setId) {
      const { data } = await ringbufferApi.updateFilterset(props.setId, payload)
      savedId = data?.id ?? props.setId
    } else {
      const { data } = await ringbufferApi.createFilterset(payload)
      savedId = data?.id
    }
    if (addToTopbar && savedId) {
      try {
        await ringbufferApi.patchFiltersetTopbar(savedId, { topbar_active: true })
      } catch (err) {
        // Surface but don't block emit — the set itself was saved.
        errorMsg.value = err?.response?.data?.detail || err?.message || 'Topbar-Update fehlgeschlagen'
      }
    }
    dirty.value = false
    emit('saved', { id: savedId, topbar: Boolean(addToTopbar) })
    emit('update:modelValue', false)
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || 'Speichern fehlgeschlagen'
  } finally {
    saving.value = false
  }
}

function onCancel() {
  if (dirty.value) {
    confirmOpen.value = true
    return
  }
  emit('update:modelValue', false)
}

function onDelete() {
  if (!props.setId || deleting.value || saving.value) return
  confirmDeleteOpen.value = true
}

async function confirmDelete() {
  if (!props.setId || deleting.value) return
  deleting.value = true
  errorMsg.value = ''
  try {
    await ringbufferApi.deleteFilterset(props.setId)
    emit('deleted', { id: props.setId })
    // Close the modal without the dirty-guard — the set is gone.
    dirty.value = false
    emit('update:modelValue', false)
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || 'Löschen fehlgeschlagen'
  } finally {
    deleting.value = false
  }
}

function confirmDiscard() {
  dirty.value = false
  emit('update:modelValue', false)
}

function onModalToggle(open) {
  if (open) return
  onCancel()
}

// Keyboard semantics inside the editor:
//   - Enter outside a text-field → "Speichern & in Topleiste"
//   - ESC inside a text-field → blur the field (so combobox dropdowns
//     close, then focus leaves the input). The modal stays open; the
//     next ESC closes it.
//   - ESC outside any text-field → handled by Modal itself, which routes
//     through onModalToggle → onCancel and respects the dirty-confirm flow.
function onKeyDown(event) {
  if (!props.modelValue) return
  // Don't interfere while the discard-confirm dialog is open — that
  // dialog owns the keyboard.
  if (confirmOpen.value) return
  const target = event.target
  const tag = target?.tagName?.toUpperCase?.() || ''
  const inEditable =
    tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || Boolean(target?.isContentEditable)

  if (event.key === 'Escape') {
    if (inEditable && typeof target.blur === 'function') {
      // Field-internal ESC handlers (e.g. combobox closing its dropdown)
      // already ran during the bubble phase; we just remove focus so the
      // next ESC closes the modal. Modal.vue skips ESC when target is
      // editable, so the modal stays open here.
      target.blur()
    }
    return
  }

  if (event.key === 'Enter') {
    // Native fields keep their own Enter behaviour (combobox option-select,
    // <select> value cycling, button activation, form submit on <input>).
    if (inEditable || tag === 'BUTTON') return
    if (filterIsEmpty.value || saving.value) return
    event.preventDefault()
    void onSave(true)
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeyDown)
})

watch(
  () => [props.modelValue, props.setId],
  async ([open, id]) => {
    if (!open) return
    if (id) {
      await loadSet(id)
    } else {
      hydrateForm(null)
    }
  },
  { immediate: true },
)
</script>
