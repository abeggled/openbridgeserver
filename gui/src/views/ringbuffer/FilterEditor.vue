<template>
  <Modal
    :model-value="modelValue"
    :soft-backdrop="softModal"
    :title="setId ? $t('ringbuffer.filterEditor.editTitle') : $t('ringbuffer.filterEditor.newTitle')"
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
        <span v-if="loadedSet.created_by === auth.username">{{ $t('ringbuffer.filterEditor.ownSet') }}</span>
        <span v-else-if="loadedSet.created_by">{{ $t('ringbuffer.filterEditor.ownerLabel') }} <strong>{{ loadedSet.created_by }}</strong></span>
        <span v-else>{{ $t('ringbuffer.filterEditor.sharedLegacy') }}</span>
        <span v-if="!canEdit" class="ml-2 inline-flex items-center rounded bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">{{ $t('ringbuffer.filterEditor.readOnly') }}</span>
      </p>

      <!-- Set-Metadaten -->
      <section class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">
            {{ $t('ringbuffer.filterEditor.nameLabel') }}
            <span class="text-red-500">*</span>
          </label>
          <input
            v-model="form.name"
            class="input"
            data-testid="filter-editor-name"
            :placeholder="$t('ringbuffer.filterEditor.namePlaceholder')"
            required
            @input="markDirty"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.descriptionLabel') }}</label>
          <input
            v-model="form.description"
            class="input"
            data-testid="filter-editor-description"
            :placeholder="$t('ringbuffer.filterEditor.descriptionPlaceholder')"
            @input="markDirty"
          />
        </div>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.colorLabel') }}</label>
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
        <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.hierarchyLabel') }}</label>
        <HierarchyCombobox
          :model-value="hierarchyIds"
          data-testid="filter-editor-hierarchy"
          @update:model-value="onHierarchyChange"
        >
          <!-- The remove (×) action is rendered by the surrounding Combobox
               wrapper — we only inject content + the ⊞ expand affordance. -->
          <template #chip="{ item, index }">
            <span class="truncate" v-bind="chipFullLabelAttrs(item)">{{ chipLabel(item) }}</span>
            <button
              type="button"
              :data-testid="`hierarchy-expand-${index}`"
              class="ml-1 text-blue-700/80 hover:text-emerald-600 dark:text-blue-300/80 dark:hover:text-emerald-300"
              :title="$t('ringbuffer.filterEditor.expandChipTitle')"
              :disabled="expanding"
              @click.stop="expandHierarchyChip(item, index)"
            >
              ⊞
            </button>
          </template>
        </HierarchyCombobox>
        <p class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.liveFilterHint') }}</p>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.datapointsLabel') }}</label>
        <DpCombobox
          :multi="true"
          :model-value="form.datapoints"
          data-testid="filter-editor-dps"
          @update:model-value="onDpsChange"
        />
        <p class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.fixedSelectionHint') }}</p>
      </section>

      <section class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.tagsLabel') }}</label>
          <TagCombobox
            :model-value="form.tags"
            data-testid="filter-editor-tags"
            @update:model-value="onTagsChange"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.adapterLabel') }}</label>
          <AdapterCombobox
            :model-value="form.adapters"
            data-testid="filter-editor-adapters"
            @update:model-value="onAdaptersChange"
          />
        </div>
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.fulltextLabel') }}</label>
        <input
          v-model="form.q"
          class="input"
          data-testid="filter-editor-q"
          :placeholder="$t('ringbuffer.filterEditor.fulltextPlaceholder')"
          @input="markDirty"
        />
      </section>

      <section class="flex flex-col gap-1">
        <label class="text-xs text-slate-500">KNX Devices (PA)</label>
        <input
          v-model="form.devicesInput"
          class="input"
          data-testid="filter-editor-devices"
          placeholder="1.1.10, 1.1.11"
          @input="markDirty"
        />
      </section>

      <!-- Wertfilter -->
      <section class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 flex flex-col gap-2">
        <h4 class="text-sm font-semibold">{{ $t('ringbuffer.filterEditor.valueFilterTitle') }}</h4>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-2">
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.datatypeLabel') }}</label>
            <select
              v-model="form.valueDataType"
              class="input"
              data-testid="filter-editor-value-type"
              @change="onValueTypeChange"
            >
              <option value="number">{{ $t('ringbuffer.filterEditor.typeNumber') }}</option>
              <option value="string">{{ $t('ringbuffer.filterEditor.typeString') }}</option>
              <option value="bool">{{ $t('ringbuffer.filterEditor.typeBool') }}</option>
              <option value="regex">{{ $t('ringbuffer.filterEditor.typeRegex') }}</option>
            </select>
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">{{ $t('ringbuffer.filterEditor.operatorLabel') }}</label>
            <select
              v-model="form.valueOperator"
              class="input"
              data-testid="filter-editor-value-operator"
              @change="markDirty"
            >
              <option value="">{{ $t('ringbuffer.filterEditor.operatorNone') }}</option>
              <option v-for="op in operatorsFor(form.valueDataType)" :key="op" :value="op">{{ op }}</option>
            </select>
          </div>
          <template v-if="form.valueOperator === 'between'">
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-500">
                {{ $t('ringbuffer.filterEditor.lowerBound') }}
                <span class="text-red-500">*</span>
              </label>
              <input
                v-model="form.valueLower"
                :class="['input', valueFilterError ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : '']"
                data-testid="filter-editor-value-lower"
                placeholder="0"
                required
                :aria-invalid="Boolean(valueFilterError)"
                @input="markDirty"
              />
            </div>
            <div class="flex flex-col gap-1">
              <label class="text-xs text-slate-500">
                {{ $t('ringbuffer.filterEditor.upperBound') }}
                <span class="text-red-500">*</span>
              </label>
              <input
                v-model="form.valueUpper"
                :class="['input', valueFilterError ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : '']"
                data-testid="filter-editor-value-upper"
                placeholder="100"
                required
                :aria-invalid="Boolean(valueFilterError)"
                @input="markDirty"
              />
            </div>
          </template>
          <template v-else-if="form.valueOperator === 'regex' || form.valueDataType === 'regex'">
            <div class="flex flex-col gap-1 md:col-span-2">
              <label class="text-xs text-slate-500">
                {{ $t('ringbuffer.filterEditor.patternLabel') }}
                <span v-if="form.valueOperator === 'regex'" class="text-red-500">*</span>
              </label>
              <input
                v-model="form.valuePattern"
                :class="['input', valueFilterError ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : '']"
                data-testid="filter-editor-value-pattern"
                :placeholder="$t('ringbuffer.filterEditor.patternPlaceholder')"
                :required="form.valueOperator === 'regex'"
                :aria-invalid="Boolean(valueFilterError)"
                @input="markDirty"
              />
            </div>
          </template>
          <template v-else-if="form.valueOperator">
            <div class="flex flex-col gap-1 md:col-span-2">
              <label class="text-xs text-slate-500">
                {{ $t('ringbuffer.filterEditor.valueLabel') }}
                <span class="text-red-500">*</span>
              </label>
              <input
                v-model="form.valueInput"
                :class="['input', valueFilterError ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : '']"
                data-testid="filter-editor-value-input"
                placeholder="42"
                required
                :aria-invalid="Boolean(valueFilterError)"
                @input="markDirty"
              />
            </div>
          </template>
        </div>
        <p v-if="valueFilterError" class="text-xs text-red-500" data-testid="filter-editor-value-error">
          {{ valueFilterError }}
        </p>
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
          {{ $t('ringbuffer.filterEditor.ignoreCase') }}
        </label>
      </section>

      <p v-if="errorMsg" data-testid="filter-editor-error" class="text-sm text-red-500">{{ errorMsg }}</p>
    </div>

    <template #footer>
      <p class="text-xs mr-auto self-center" data-testid="filter-editor-semantics-hint"
         :class="filterIsEmpty || valueFilterError ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500'">
        <span v-if="valueFilterError" data-testid="filter-editor-validation-hint">
          {{ valueFilterError }}
        </span>
        <span v-else-if="filterIsEmpty" data-testid="filter-editor-empty-hint">
          {{ $t('ringbuffer.filterEditor.emptyFilterWarning') }}
        </span>
        <span v-else>
          {{ $t('ringbuffer.filterEditor.semanticsHint') }}
        </span>
      </p>
      <button
        v-if="setId"
        class="btn-danger btn-sm"
        :disabled="deleting || saving || !canEdit"
        data-testid="filter-editor-delete"
        :title="canEdit ? $t('ringbuffer.filterEditor.deleteTitle') : $t('ringbuffer.filterEditor.deleteRestricted')"
        @click="onDelete"
      >
        🗑 {{ $t('common.delete') }}
      </button>
      <button class="btn-secondary btn-sm" data-testid="filter-editor-cancel" @click="onCancel">{{ $t('ringbuffer.filterEditor.discard') }}</button>
      <button
        class="btn-primary btn-sm"
        :disabled="saving || filterIsEmpty || Boolean(valueFilterError) || deleting || !canEdit"
        data-testid="filter-editor-save-topbar"
        :title="canEdit ? '' : $t('ringbuffer.filterEditor.saveRestricted')"
        @click="onSave(true)"
      >
        {{ $t('ringbuffer.filterEditor.saveAndTopbar') }}
      </button>
    </template>
  </Modal>

  <ConfirmDialog
    v-model="confirmOpen"
    :title="$t('ringbuffer.filterEditor.unsavedTitle')"
    :message="$t('ringbuffer.filterEditor.unsavedMessage')"
    :confirm-label="$t('ringbuffer.filterEditor.discard')"
    @confirm="confirmDiscard"
  />
  <ConfirmDialog
    v-model="confirmDeleteOpen"
    :title="$t('ringbuffer.filterEditor.deleteSetTitle')"
    :message="deleteSetMessage"
    :confirm-label="$t('common.delete')"
    :danger="true"
    @confirm="confirmDelete"
  />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ringbufferApi, searchApi, hierarchyApi } from '@/api/client'
import Modal from '@/components/ui/Modal.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import HierarchyCombobox from '@/components/ui/HierarchyCombobox.vue'
import DpCombobox from '@/components/ui/DpCombobox.vue'
import TagCombobox from '@/components/ui/TagCombobox.vue'
import AdapterCombobox from '@/components/ui/AdapterCombobox.vue'
import { isEmptyFilter } from '@/composables/useClientSideMatch'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
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
    devicesInput: '',
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

function valueFilterValidationKey() {
  if (!form.valueOperator) return ''
  if (form.valueOperator === 'between') {
    const lowerText = form.valueLower.trim()
    const upperText = form.valueUpper.trim()
    if (!lowerText || !upperText) return 'ringbuffer.filterEditor.valueBetweenRequired'
    const lower = Number(lowerText)
    const upper = Number(upperText)
    if (!Number.isFinite(lower) || !Number.isFinite(upper)) return 'ringbuffer.filterEditor.valueNumberRequired'
    if (lower > upper) return 'ringbuffer.filterEditor.valueRangeInvalid'
    return ''
  }
  if (form.valueOperator === 'regex') {
    const pattern = form.valuePattern.trim()
    if (!pattern) return 'ringbuffer.filterEditor.valuePatternRequired'
    return ''
  }

  const text = form.valueInput.trim()
  if (!text) return 'ringbuffer.filterEditor.valueRequired'
  if (form.valueDataType === 'number') {
    return Number.isFinite(Number(text)) ? '' : 'ringbuffer.filterEditor.valueNumberRequired'
  }
  if (form.valueDataType === 'bool') {
    return ['true', 'false', '1', '0'].includes(text.toLowerCase())
      ? ''
      : 'ringbuffer.filterEditor.valueBoolRequired'
  }
  return ''
}

const valueFilterError = computed(() => {
  const key = valueFilterValidationKey()
  return key ? t(key) : ''
})

// Disable Save when the filter has no populated criteria — matches backend
// validation (POST/PUT /filtersets reject empty FilterCriteria with 422).
const filterIsEmpty = computed(() =>
  isEmptyFilter({
    hierarchy_nodes: form.hierarchy_nodes,
    datapoints: form.datapoints,
    devices: parseDeviceAddresses(form.devicesInput),
    tags: form.tags,
    adapters: form.adapters,
    q: form.q,
    value_filter: buildValueFilter(),
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

const deleteSetMessage = computed(() =>
  t('ringbuffer.filterEditor.deleteSetMessage', { name: loadedSet.value?.name ?? '' }),
)
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
  if (!item) return ''
  if (Array.isArray(item.display_path) && item.display_path.length) {
    return item.display_path.join(' › ')
  }
  if (item.label) return item.label

  const path = Array.isArray(item.path) ? item.path : []
  if (path.length === 0) return item.name ?? String(item.id ?? '')
  const leaf = path[path.length - 1]
  const depth = Number(item.display_depth) || 0
  if (depth > 0) {
    const startIndex = depth - 1
    if (path.length > startIndex) return path.slice(startIndex).join(' › ')
  }
  return item.tree_name ? `${item.tree_name} › ${leaf}` : leaf
}

function chipFullLabel(item) {
  if (!item) return ''
  if (item.full_label) return item.full_label
  const path = Array.isArray(item.path) ? item.path : []
  const parts = [item.tree_name, ...path].filter(Boolean)
  return parts.length ? parts.join(' › ') : chipLabel(item)
}

function chipFullLabelAttrs(item) {
  return { title: chipFullLabel(item) }
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
      devices: parseDeviceAddresses(form.devicesInput),
      tags: [...form.tags],
      adapters: [...form.adapters],
      q: form.q ? form.q.trim() : null,
      value_filter: buildValueFilter(),
    },
  }
}

function parseDeviceAddresses(raw) {
  return Array.from(
    new Set(
      String(raw ?? '')
        .split(/[,\s]+/g)
        .map((part) => part.trim())
        .filter(Boolean),
    ),
  )
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
  form.devicesInput = Array.isArray(flt.devices) ? flt.devices.map((v) => String(v).trim()).filter(Boolean).join(', ') : ''
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
    errorMsg.value = err?.response?.data?.detail || err?.message || t('ringbuffer.filterEditor.loadError')
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
    errorMsg.value = err?.response?.data?.detail || err?.message || t('ringbuffer.filterEditor.expandError')
  } finally {
    expanding.value = false
  }
  // suppress unused-arg warning while still documenting the slot contract
  void item
}

async function onSave(addToTopbar) {
  errorMsg.value = ''
  if (!form.name.trim()) {
    errorMsg.value = t('ringbuffer.filterEditor.nameRequired')
    return
  }
  if (valueFilterError.value) {
    errorMsg.value = valueFilterError.value
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
        errorMsg.value = err?.response?.data?.detail || err?.message || t('ringbuffer.filterEditor.topbarUpdateFailed')
      }
    }
    dirty.value = false
    emit('saved', { id: savedId, topbar: Boolean(addToTopbar) })
    emit('update:modelValue', false)
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || t('ringbuffer.filterEditor.saveFailed')
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
    errorMsg.value = err?.response?.data?.detail || err?.message || t('ringbuffer.filterEditor.deleteFailed')
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
