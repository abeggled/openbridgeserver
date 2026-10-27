<template>
  <div class="flex flex-col gap-4" data-testid="ha-import">
    <!-- Filters -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
      <div class="form-group col-span-2">
        <label class="label">{{ $t('adapters.homeassistant.search') }}</label>
        <input v-model="filters.q" class="input text-sm" :placeholder="$t('adapters.homeassistant.searchPlaceholder')" data-testid="ha-filter-q" @input="onQueryInput" @keyup.enter="load(0)" />
      </div>
      <div v-for="facet in FACETS" :key="facet" class="form-group">
        <label class="label">{{ $t(`adapters.homeassistant.facet.${facet}`) }}</label>
        <select v-model="filters[facet]" class="input text-sm" :data-testid="`ha-filter-${facet}`" @change="load(0)">
          <option value="">{{ $t('adapters.homeassistant.all') }}</option>
          <option v-for="v in facets[facet] || []" :key="v" :value="v">{{ v }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.homeassistant.availability') }}</label>
        <select v-model="filters.availability" class="input text-sm" data-testid="ha-filter-availability" @change="load(0)">
          <option value="">{{ $t('adapters.homeassistant.all') }}</option>
          <option value="available">{{ $t('adapters.homeassistant.available') }}</option>
          <option value="unavailable">{{ $t('adapters.homeassistant.unavailable') }}</option>
        </select>
      </div>
    </div>

    <div class="flex items-center gap-3 text-sm">
      <HelpButton help-id="adapters-homeassistant" />
      <button class="btn-secondary btn-sm" :disabled="busy !== null" data-testid="ha-load" @click="load(0)">
        <Spinner v-if="busy === 'load'" size="xs" color="slate" />
        {{ $t('adapters.homeassistant.load') }}
      </button>
      <span v-if="loaded" class="text-slate-500" data-testid="ha-hits">{{ $t('adapters.homeassistant.hits', { n: total }) }}</span>
      <span v-if="loaded && !metadataAvailable" class="text-amber-500 text-xs" data-testid="ha-no-metadata">{{ $t('adapters.homeassistant.metadataUnavailable') }}</span>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400" data-testid="ha-error">{{ error }}</div>

    <!-- Entity list -->
    <div v-if="items.length" class="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      <div class="max-h-80 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700/50">
        <div v-for="item in items" :key="item.entity_id" class="px-3 py-2" :data-testid="`ha-entity-${item.entity_id}`">
          <div class="flex items-center gap-2 min-w-0">
            <button class="btn-icon shrink-0" :aria-label="$t('adapters.homeassistant.toggleDetails')" :data-testid="`ha-expand-${item.entity_id}`" @click="toggleExpanded(item.entity_id)">
              <svg class="w-3 h-3 transition-transform" :class="{ 'rotate-90': expanded[item.entity_id] }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
            </button>
            <input type="checkbox" class="w-4 h-4 rounded" :checked="isSelected(defaultSelection(item))" :data-testid="`ha-select-${item.entity_id}`" @change="toggle(defaultSelection(item), $event.target.checked)" />
            <span class="text-sm truncate">{{ item.name }}</span>
            <span class="font-mono text-xs text-slate-500 truncate">{{ item.entity_id }}</span>
            <Badge v-if="!item.available" variant="danger" size="xs">{{ $t('adapters.homeassistant.unavailable') }}</Badge>
            <span class="ml-auto font-mono text-xs text-slate-500 shrink-0">{{ item.state }} {{ item.unit || '' }}</span>
          </div>
          <div class="pl-12 text-xs text-slate-500 truncate">
            {{ [item.area, item.device, item.platform, item.device_class].filter(Boolean).join(' · ') }}
          </div>
          <div v-if="expanded[item.entity_id]" class="pl-12 pt-2 flex flex-col gap-1 text-xs">
            <div class="font-semibold text-slate-600 dark:text-slate-300">{{ $t('adapters.homeassistant.profiles') }}</div>
            <label class="flex items-center gap-2">
              <input type="checkbox" class="w-3.5 h-3.5 rounded" :checked="isSelected(plainSelection(item))" :data-testid="`ha-plain-${item.entity_id}`" @change="toggle(plainSelection(item), $event.target.checked)" />
              {{ $t('adapters.homeassistant.plainState') }}
            </label>
            <label v-for="p in item.profiles" :key="p.id" class="flex items-center gap-2" :class="{ 'opacity-50': !p.supported }">
              <input
                type="checkbox"
                class="w-3.5 h-3.5 rounded"
                :disabled="!p.supported"
                :checked="isSelected(profileSelection(item, p.id))"
                :data-testid="`ha-profile-${item.entity_id}-${p.id}`"
                @change="toggle(profileSelection(item, p.id), $event.target.checked)"
              />
              {{ $t(profileLabelKey(p.id)) }}
              <span v-if="p.id === item.default_profile" class="text-blue-500">{{ $t('adapters.homeassistant.proposed') }}</span>
              <span v-if="!p.supported" class="text-amber-500">{{ $t(reasonKey(p.reason)) }}</span>
            </label>
            <template v-if="attributeNames(item).length">
              <div class="font-semibold text-slate-600 dark:text-slate-300 pt-1">{{ $t('adapters.homeassistant.attributes') }}</div>
              <label v-for="name in attributeNames(item)" :key="name" class="flex items-center gap-2">
                <input type="checkbox" class="w-3.5 h-3.5 rounded" :checked="isSelected(attributeSelection(item, name))" :data-testid="`ha-attr-${item.entity_id}-${name}`" @change="toggle(attributeSelection(item, name), $event.target.checked)" />
                <span class="font-mono">{{ name }}</span>
                <span class="text-slate-500 truncate">{{ String(item.attributes[name]) }}</span>
              </label>
            </template>
          </div>
        </div>
      </div>
      <div v-if="paged" class="px-3 py-2 bg-slate-50 dark:bg-slate-800/60 flex items-center gap-2 text-xs">
        <button class="btn-secondary btn-sm" :disabled="offset === 0 || busy !== null" data-testid="ha-prev" @click="load(Math.max(0, offset - LIMIT))">{{ $t('adapters.homeassistant.prev') }}</button>
        <button class="btn-secondary btn-sm" :disabled="!hasNext || busy !== null" data-testid="ha-next" @click="load(offset + LIMIT)">{{ $t('adapters.homeassistant.next') }}</button>
      </div>
    </div>
    <p v-else-if="loaded && !error" class="text-sm text-slate-500" data-testid="ha-empty">{{ $t('adapters.homeassistant.noEntities') }}</p>

    <!-- Import options -->
    <div class="grid grid-cols-3 gap-3">
      <div class="form-group">
        <label class="label">{{ $t('adapters.homeassistant.tags') }}</label>
        <input v-model="options.tags" class="input text-sm" :placeholder="$t('adapters.homeassistant.tagsPlaceholder')" data-testid="ha-tags" />
      </div>
      <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-7">
        <input v-model="options.persist_value" type="checkbox" class="w-4 h-4 rounded" />
        {{ $t('datapoints.form.persistValue') }}
      </label>
      <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-7">
        <input v-model="options.record_history" type="checkbox" class="w-4 h-4 rounded" />
        {{ $t('datapoints.detail.recordHistory') }}
      </label>
    </div>

    <div class="flex items-center gap-3">
      <button class="btn-secondary btn-sm" :disabled="busy !== null || !selections.length" data-testid="ha-preview" @click="preview">
        <Spinner v-if="busy === 'preview'" size="xs" color="slate" />
        {{ $t('adapters.homeassistant.preview', { n: selections.length }) }}
      </button>
      <button class="btn-primary btn-sm" :disabled="busy !== null || importableCount === 0" data-testid="ha-import-btn" @click="runImport">
        <Spinner v-if="busy === 'import'" size="xs" color="white" />
        {{ $t('adapters.homeassistant.import', { n: importableCount }) }}
      </button>
    </div>

    <div v-if="result" class="p-3 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-500" data-testid="ha-result">
      {{ $t('adapters.homeassistant.importResult', {
        datapoints: result.created_datapoints,
        bindings: result.created_bindings,
        existing: result.skipped_existing,
        conflicts: result.skipped_conflicts,
        unsupported: result.skipped_unsupported,
      }) }}
      <div v-for="e in result.errors" :key="e" class="text-red-400">{{ e }}</div>
      <div v-if="refreshFailed" class="text-amber-500" data-testid="ha-refresh-failed">{{ $t('adapters.homeassistant.refreshFailed') }}</div>
    </div>

    <!-- Preview -->
    <div v-if="plan.length" class="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden" data-testid="ha-plan">
      <div class="px-3 py-2 bg-slate-50 dark:bg-slate-800/60 text-xs text-slate-500">{{ $t('adapters.homeassistant.nameHint') }}</div>
      <div class="max-h-96 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700/50">
        <div v-for="(c, i) in plan" :key="i" class="px-3 py-2 text-xs flex flex-col gap-0.5" :class="{ 'opacity-60': c.status !== 'new' }" :data-testid="`ha-plan-${i}`">
          <div class="flex items-center gap-2 min-w-0">
            <Badge :variant="STATUS_VARIANT[c.status]" size="xs">{{ $t(`adapters.homeassistant.status.${c.status}`) }}</Badge>
            <template v-if="c.status === 'new'">
              <input
                :value="c.name"
                maxlength="255"
                class="input py-0.5 text-sm flex-1 min-w-0"
                :aria-label="$t('adapters.homeassistant.nameLabel')"
                :data-testid="`ha-name-${i}`"
                @input="setName(c, $event.target.value)"
                @blur="normalizeName(c)"
              />
              <button
                v-if="c.name !== c.proposed_name"
                type="button"
                class="btn-icon shrink-0"
                :title="$t('adapters.homeassistant.resetName', { name: c.proposed_name })"
                :aria-label="$t('adapters.homeassistant.resetName', { name: c.proposed_name })"
                :data-testid="`ha-name-reset-${i}`"
                @click="resetName(c)"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              </button>
            </template>
            <span v-else class="text-sm truncate">{{ c.name }}</span>
            <Badge variant="info" size="xs">{{ c.data_type }}</Badge>
            <Badge variant="muted" size="xs">{{ c.direction }}</Badge>
            <span v-if="c.unit" class="text-slate-500">{{ c.unit }}</span>
          </div>
          <div class="font-mono text-slate-500 truncate">
            {{ c.entity_id }}{{ c.attribute ? ` / ${c.attribute}` : '' }}
            <template v-if="c.profile">{{ $t('adapters.homeassistant.viaProfile', { profile: $t(profileLabelKey(c.profile)) }) }}</template>
          </div>
          <div v-if="c.convention" class="text-blue-500">{{ $t(conventionKey(c.convention)) }}</div>
          <template v-if="c.mapping">
            <div class="text-slate-500">
              {{ $t('adapters.homeassistant.mappingRead', { source: c.mapping.read.attribute || 'state' }) }}
              <span v-if="c.mapping.read.state_map" class="font-mono">{{ JSON.stringify(c.mapping.read.state_map) }}</span>
            </div>
            <div v-for="row in writeRows(c.mapping.write, c.mapping.write_kind)" :key="row.when ?? 'all'" class="font-mono text-slate-600 dark:text-slate-300">
              {{ $t(row.when === null ? 'adapters.homeassistant.writeAlways' : 'adapters.homeassistant.writeWhen', { when: row.when, action: row.action }) }}
              <span v-if="row.valuePath">{{ $t('adapters.homeassistant.writeValuePath', { path: row.valuePath }) }}</span>
            </div>
          </template>
          <div class="text-slate-500 truncate">{{ $t('adapters.homeassistant.tagsList', { tags: c.tags.join(', ') }) }}</div>
          <div v-if="c.reason" :class="c.status === 'new' ? 'text-slate-500' : 'text-amber-500'">{{ $t(reasonKey(c.reason)) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { adapterApi } from '@/api/client'
import Badge from '@/components/ui/Badge.vue'
import HelpButton from '@/components/ui/HelpButton.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { conventionKey, profileLabelKey, reasonKey, writeRows } from '@/utils/haMapping'

const props = defineProps({
  instance: { type: Object, required: true },
})

const { t } = useI18n()

const FACETS = ['domain', 'area', 'platform', 'device']
const LIMIT = 50
const STATUS_VARIANT = { new: 'success', exists: 'muted', conflict: 'warning', unsupported: 'danger' }

const filters = reactive({ q: '', domain: '', area: '', platform: '', device: '', availability: '' })
const options = reactive({ tags: '', persist_value: true, record_history: true })
const items = ref([])
const facets = ref({})
const total = ref(0)
const offset = ref(0)
const loaded = ref(false)
const metadataAvailable = ref(true)
const busy = ref(null)
const error = ref(null)
const expanded = reactive({})
const selected = ref(new Map())
const plan = ref([])
const result = ref(null)
const refreshFailed = ref(false)

const selections = computed(() => [...selected.value.values()])
const importableCount = computed(() => plan.value.filter(c => c.status === 'new').length)
const hasNext = computed(() => offset.value + LIMIT < total.value)
const paged = computed(() => hasNext.value || offset.value > 0)

function selectionKey(s) {
  return `${s.entity_id}|${s.attribute ?? ''}|${s.profile === null ? '-' : s.profile}`
}
function plainSelection(item) {
  return { entity_id: item.entity_id, attribute: null, profile: null }
}
function profileSelection(item, profileId) {
  return { entity_id: item.entity_id, attribute: null, profile: profileId }
}
function attributeSelection(item, name) {
  return { entity_id: item.entity_id, attribute: name, profile: 'auto' }
}
function defaultSelection(item) {
  return item.default_profile ? profileSelection(item, item.default_profile) : plainSelection(item)
}
function isSelected(s) {
  return selected.value.has(selectionKey(s))
}
function toggle(s, on) {
  const next = new Map(selected.value)
  if (on) next.set(selectionKey(s), s)
  else {
    next.delete(selectionKey(s))
    delete nameOverrides[selectionKey(s)]
  }
  selected.value = next
  plan.value = []
}
function toggleExpanded(entityId) {
  expanded[entityId] = !expanded[entityId]
}
function attributeNames(item) {
  return Object.keys(item.attributes || {}).filter(k => k !== 'friendly_name' && k !== 'supported_features').sort()
}

function errorText(e, fallbackKey) {
  const detail = e?.response?.data?.detail
  // FastAPI validation errors (422) carry a list of { loc, msg } objects
  if (Array.isArray(detail)) return detail.map(d => d?.msg ?? String(d)).join('; ') || t(fallbackKey)
  return detail ?? t(fallbackKey)
}

// Live search while typing; Enter still searches immediately.
let queryTimer = null
function onQueryInput() {
  clearTimeout(queryTimer)
  queryTimer = setTimeout(() => load(0), 300)
}

let loadSeq = 0

async function load(newOffset) {
  clearTimeout(queryTimer)
  const mine = ++loadSeq
  busy.value = 'load'
  error.value = null
  try {
    const params = { ...filters, offset: newOffset, limit: LIMIT }
    const { data } = await adapterApi.haEntities(props.instance.id, params)
    if (mine !== loadSeq) return  // a newer search superseded this one
    items.value = data.items
    facets.value = data.facets
    total.value = data.total
    metadataAvailable.value = data.metadata_available
    offset.value = newOffset
    loaded.value = true
  } catch (e) {
    if (mine === loadSeq) error.value = errorText(e, 'adapters.homeassistant.loadError')
  } finally {
    if (mine === loadSeq) busy.value = null
  }
}

// Object names edited in the preview, keyed like the selection they belong to.
const nameOverrides = reactive({})

function setName(c, value) {
  c.name = value
  const key = selectionKey(selections.value[c.selection])
  const name = value.trim()
  if (name && name !== c.proposed_name) nameOverrides[key] = name
  else delete nameOverrides[key]
}
function normalizeName(c) {
  if (!c.name.trim()) c.name = c.proposed_name
}
function resetName(c) {
  setName(c, c.proposed_name)
}

function payload() {
  return {
    selections: selections.value.map(s => {
      const name = nameOverrides[selectionKey(s)]
      return name ? { ...s, name } : s
    }),
    tags: options.tags.split(',').map(s => s.trim()).filter(Boolean),
    persist_value: options.persist_value,
    record_history: options.record_history,
  }
}

async function preview() {
  busy.value = 'preview'
  error.value = null
  result.value = null
  try {
    const { data } = await adapterApi.haImportPreview(props.instance.id, payload())
    plan.value = data.preview
  } catch (e) {
    error.value = errorText(e, 'adapters.homeassistant.previewError')
  } finally {
    busy.value = null
  }
}

async function runImport() {
  busy.value = 'import'
  error.value = null
  refreshFailed.value = false
  try {
    const { data } = await adapterApi.haImport(props.instance.id, payload())
    result.value = data
  } catch (e) {
    error.value = errorText(e, 'adapters.homeassistant.importFailed')
    busy.value = null
    return
  }
  try {
    // Re-plan so what was just created shows as existing.
    const { data: fresh } = await adapterApi.haImportPreview(props.instance.id, payload())
    plan.value = fresh.preview
  } catch {
    // The import itself succeeded: never offer the stale plan for a second import.
    plan.value = []
    refreshFailed.value = true
  } finally {
    busy.value = null
  }
}

onMounted(() => load(0))
onUnmounted(() => clearTimeout(queryTimer))

defineExpose({ load })
</script>
