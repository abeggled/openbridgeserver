<template>
  <div class="flex flex-col gap-5 h-full min-h-0">
    <div class="flex flex-wrap items-start gap-3 shrink-0">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ $t('messageArchives.title') }}</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ $t('messageArchives.subtitle') }}</p>
      </div>
      <div v-if="auth.isAdmin" class="flex items-center gap-2">
        <button class="btn-secondary btn-sm" @click="runIntegrityCheck">{{ $t('messageArchives.integrityCheck') }}</button>
        <button class="btn-primary btn-sm" @click="startCreate">{{ $t('messageArchives.newArchive') }}</button>
      </div>
    </div>

    <div v-if="error" class="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-200">
      {{ error }}
    </div>
    <div v-if="integrityResult" class="rounded-lg border border-slate-200 dark:border-slate-700 px-4 py-3 text-sm text-slate-600 dark:text-slate-300">
      {{ $t('messageArchives.integrityResult') }}: {{ integrityResult.result }}
    </div>

    <div class="grid gap-4 xl:grid-cols-[22rem_1fr] min-h-0 flex-1">
      <section class="card p-0 overflow-hidden min-h-0 flex flex-col">
        <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-200">{{ $t('messageArchives.archives') }}</h3>
          <span class="text-xs text-slate-500">{{ archives.length }}</span>
        </div>
        <div v-if="loading" class="p-6 text-sm text-slate-500">{{ $t('common.loading') }}</div>
        <div v-else-if="!archives.length" class="p-6 text-sm text-slate-500">{{ $t('messageArchives.noArchives') }}</div>
        <template v-else>
          <button
            v-for="archive in archives"
            :key="archive.id"
            type="button"
            :class="[
              'w-full text-left px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 hover:bg-slate-50 dark:hover:bg-slate-800/50',
              selectedArchive?.id === archive.id ? 'bg-blue-50 dark:bg-blue-500/10' : '',
            ]"
            @click="selectArchive(archive)"
          >
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full" :style="{ backgroundColor: archive.color }" />
              <span class="font-medium text-sm text-slate-800 dark:text-slate-100 truncate">{{ archive.name }}</span>
              <span class="ml-auto text-xs text-slate-500">{{ archive.entry_count }}</span>
            </div>
            <div class="mt-1 text-xs text-slate-500 truncate">{{ archive.id }}</div>
          </button>
        </template>
      </section>

      <section class="min-h-0 flex flex-col gap-4">
        <div class="card p-4">
          <div v-if="editing" class="grid gap-3 md:grid-cols-2">
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.name') }}
              <input v-model="form.name" class="mt-1 input w-full" @input="onNameInput" />
            </label>
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.id') }}
              <input
                v-model="form.id"
                :disabled="!!selectedArchive"
                class="mt-1 input w-full"
                @input="archiveIdManuallyEdited = true"
              />
            </label>
            <label class="text-xs text-slate-500 md:col-span-2">
              {{ $t('messageArchives.form.description') }}
              <input v-model="form.description" class="mt-1 input w-full" />
            </label>
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.defaultType') }}
              <select v-model="form.default_type" class="mt-1 input w-full">
                <option value="">{{ $t('messageArchives.form.defaultTypeNone') }}</option>
                <option v-for="type in MESSAGE_TYPES" :key="type" :value="type">{{ typeLabel(type) }}</option>
                <option value="__custom">{{ $t('messageArchives.form.defaultTypeCustom') }}</option>
              </select>
              <input
                v-if="form.default_type === '__custom'"
                v-model="form.custom_default_type"
                class="mt-2 input w-full"
                :placeholder="$t('messageArchives.form.defaultTypeCustomPlaceholder')"
              />
              <span class="mt-1 block text-[11px] text-slate-400">{{ $t('messageArchives.form.defaultTypeHint') }}</span>
            </label>
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.color') }}
              <input v-model="form.color" type="color" class="mt-1 h-9 w-full bg-transparent" />
            </label>
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.maxEntries') }}
              <input v-model.number="form.retention_max_entries" type="number" min="1" class="mt-1 input w-full" />
            </label>
            <label class="text-xs text-slate-500">
              {{ $t('messageArchives.form.maxAgeDays') }}
              <input v-model.number="form.retention_max_age_days" type="number" min="1" class="mt-1 input w-full" />
            </label>
            <div class="md:col-span-2 flex justify-end gap-2">
              <button class="btn-secondary btn-sm" @click="cancelEdit">{{ $t('common.cancel') }}</button>
              <button class="btn-primary btn-sm" @click="saveArchive">{{ $t('common.save') }}</button>
            </div>
          </div>
          <div v-else-if="selectedArchive" class="flex flex-wrap items-start gap-3">
            <div class="flex-1">
              <div class="flex items-center gap-2">
                <span class="w-3 h-3 rounded-full" :style="{ backgroundColor: selectedArchive.color }" />
                <h3 class="text-lg font-semibold text-slate-800 dark:text-slate-100">{{ selectedArchive.name }}</h3>
              </div>
              <p class="mt-1 text-sm text-slate-500">{{ selectedArchive.description || selectedArchive.id }}</p>
              <p class="mt-2 text-xs text-slate-500">
                {{ $t('messageArchives.dbPath') }}: <span class="font-mono">{{ selectedArchive.db_path }}</span>
              </p>
            </div>
            <div v-if="auth.isAdmin" class="flex flex-wrap gap-2">
              <button class="btn-secondary btn-sm" @click="startEdit(selectedArchive)">{{ $t('common.edit') }}</button>
              <button class="btn-secondary btn-sm" @click="exportArchive('jsonl')">{{ $t('messageArchives.exportJsonl') }}</button>
              <button class="btn-secondary btn-sm" @click="exportArchive('csv')">{{ $t('messageArchives.exportCsv') }}</button>
              <button class="btn-secondary btn-sm text-amber-600" @click="clearArchive(selectedArchive)">{{ $t('messageArchives.clear') }}</button>
              <button class="btn-secondary btn-sm text-red-600" @click="deleteArchive(selectedArchive)">{{ $t('common.delete') }}</button>
            </div>
          </div>
          <div v-else class="text-sm text-slate-500">{{ $t('messageArchives.selectArchive') }}</div>
        </div>

        <div class="card p-0 overflow-hidden min-h-0 flex flex-col">
          <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 flex flex-wrap gap-2 items-center">
            <input v-model="filters.q" class="input text-sm w-48" :placeholder="$t('messageArchives.filters.search')" @keyup.enter="loadEntries" />
            <select v-model="filters.severity" class="input text-sm w-36" @change="loadEntries">
              <option value="">{{ $t('messageArchives.filters.allSeverities') }}</option>
              <option v-for="severity in MESSAGE_SEVERITIES" :key="severity" :value="severity">{{ severityLabel(severity) }}</option>
            </select>
            <select v-model="filters.status" class="input text-sm w-36" @change="loadEntries">
              <option value="">{{ $t('messageArchives.filters.allStatuses') }}</option>
              <option v-for="status in MESSAGE_STATUSES" :key="status" :value="status">{{ statusLabel(status) }}</option>
            </select>
            <select v-model="filters.type" class="input text-sm w-40" @change="loadEntries">
              <option value="">{{ $t('messageArchives.filters.allTypes') }}</option>
              <option v-for="type in MESSAGE_TYPES" :key="type" :value="type">{{ typeLabel(type) }}</option>
            </select>
            <button class="btn-secondary btn-sm" @click="loadEntries">{{ $t('messageArchives.refresh') }}</button>
            <span class="ml-auto text-xs text-slate-500">{{ entriesTotal }} {{ $t('messageArchives.entries') }}</span>
          </div>
          <div v-if="entriesLoading" class="p-6 text-sm text-slate-500">{{ $t('common.loading') }}</div>
          <div v-else-if="!entries.length" class="p-6 text-sm text-slate-500">{{ $t('messageArchives.noEntries') }}</div>
          <div v-else class="overflow-auto min-h-0">
            <table class="table">
              <thead>
                <tr>
                  <th>{{ $t('messageArchives.table.time') }}</th>
                  <th>{{ $t('messageArchives.table.message') }}</th>
                  <th>{{ $t('messageArchives.table.type') }}</th>
                  <th>{{ $t('messageArchives.table.severity') }}</th>
                  <th>{{ $t('messageArchives.table.status') }}</th>
                  <th>{{ $t('messageArchives.table.source') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="entry in entries" :key="entry.id">
                  <td class="font-mono text-xs text-slate-500 whitespace-nowrap">{{ fmt(entry.created_at) }}</td>
                  <td>
                    <div class="font-medium text-sm text-slate-800 dark:text-slate-100">{{ entry.title || '-' }}</div>
                    <div class="text-xs text-slate-500">{{ entry.message }}</div>
                  </td>
                  <td class="text-xs">{{ typeLabel(entry.type) }}</td>
                  <td class="text-xs">{{ severityLabel(entry.severity) }}</td>
                  <td class="text-xs">{{ statusLabel(entry.status) }}</td>
                  <td class="text-xs text-slate-500">{{ entry.source || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { messageArchivesApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const { t, te } = useI18n()
const auth = useAuthStore()
const MESSAGE_TYPES = ['system', 'security', 'notification', 'automation', 'adapter', 'diagnostic']
const MESSAGE_SEVERITIES = ['info', 'success', 'warning', 'error', 'critical']
const MESSAGE_STATUSES = ['new', 'open', 'acknowledged', 'closed']

const archives = ref([])
const selectedArchive = ref(null)
const entries = ref([])
const entriesTotal = ref(0)
const loading = ref(false)
const entriesLoading = ref(false)
const editing = ref(false)
const error = ref('')
const integrityResult = ref(null)
const archiveIdManuallyEdited = ref(false)

const form = reactive({
  id: '',
  name: '',
  description: '',
  default_type: '',
  custom_default_type: '',
  color: '#3b82f6',
  retention_max_entries: null,
  retention_max_age_days: null,
})

const filters = reactive({
  q: '',
  severity: '',
  status: '',
  type: '',
})

const currentArchiveId = computed(() => selectedArchive.value?.id ?? '')

function fmt(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function typeLabel(value) {
  const key = `messageArchives.types.${value}`
  return te(key) ? t(key) : value
}

function severityLabel(value) {
  const key = `messageArchives.severities.${value}`
  return te(key) ? t(key) : value
}

function statusLabel(value) {
  const key = `messageArchives.statuses.${value}`
  return te(key) ? t(key) : value
}

function resetForm() {
  Object.assign(form, {
    id: '',
    name: '',
    description: '',
    default_type: '',
    custom_default_type: '',
    color: '#3b82f6',
    retention_max_entries: null,
    retention_max_age_days: null,
  })
  archiveIdManuallyEdited.value = false
}

function archiveIdFromName(name) {
  return name
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9._-]/g, '')
    .replace(/-+/g, '-')
    .slice(0, 80)
}

function onNameInput() {
  if (selectedArchive.value || archiveIdManuallyEdited.value) return
  form.id = archiveIdFromName(form.name)
}

function formPayload() {
  const defaultType = form.default_type === '__custom'
    ? form.custom_default_type.trim().toLowerCase()
    : form.default_type
  return {
    id: form.id.trim().toLowerCase(),
    name: form.name.trim(),
    description: form.description,
    tags: [],
    default_type: defaultType || null,
    color: form.color || '#3b82f6',
    retention_max_entries: form.retention_max_entries || null,
    retention_max_age_days: form.retention_max_age_days || null,
  }
}

async function loadArchives() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await messageArchivesApi.list()
    archives.value = data
    if (selectedArchive.value) {
      selectedArchive.value = data.find(a => a.id === selectedArchive.value.id) ?? null
    }
    if (!selectedArchive.value && data.length) selectedArchive.value = data[0]
    if (selectedArchive.value) await loadEntries()
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || t('common.loadError')
  } finally {
    loading.value = false
  }
}

async function loadEntries() {
  if (!currentArchiveId.value) {
    entries.value = []
    entriesTotal.value = 0
    return
  }
  entriesLoading.value = true
  try {
    const params = {
      archive_id: currentArchiveId.value,
      limit: 200,
    }
    if (filters.q) params.q = filters.q
    if (filters.severity) params.severity = filters.severity
    if (filters.status) params.status = filters.status
    if (filters.type) params.type = filters.type
    const { data } = await messageArchivesApi.entries(params)
    entries.value = data.items
    entriesTotal.value = data.total
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || t('common.loadError')
  } finally {
    entriesLoading.value = false
  }
}

function selectArchive(archive) {
  selectedArchive.value = archive
  editing.value = false
  loadEntries()
}

function startCreate() {
  if (!auth.isAdmin) return
  selectedArchive.value = null
  resetForm()
  editing.value = true
}

function startEdit(archive) {
  if (!auth.isAdmin) return
  const knownType = MESSAGE_TYPES.includes(archive.default_type)
  Object.assign(form, {
    id: archive.id,
    name: archive.name,
    description: archive.description,
    default_type: knownType ? archive.default_type : (archive.default_type ? '__custom' : ''),
    custom_default_type: knownType ? '' : (archive.default_type || ''),
    color: archive.color || '#3b82f6',
    retention_max_entries: archive.retention_max_entries,
    retention_max_age_days: archive.retention_max_age_days,
  })
  archiveIdManuallyEdited.value = true
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  resetForm()
}

async function saveArchive() {
  if (!auth.isAdmin) return
  error.value = ''
  try {
    const payload = formPayload()
    if (selectedArchive.value) {
      const { id, ...patch } = payload
      await messageArchivesApi.update(selectedArchive.value.id, patch)
    } else {
      await messageArchivesApi.create(payload)
    }
    editing.value = false
    await loadArchives()
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || t('common.saveError')
  }
}

async function clearArchive(archive) {
  if (!auth.isAdmin) return
  if (!window.confirm(t('messageArchives.confirmClear', { count: archive.entry_count }))) return
  await messageArchivesApi.clear(archive.id, true)
  await loadArchives()
}

async function deleteArchive(archive) {
  if (!auth.isAdmin) return
  if (!window.confirm(t('messageArchives.confirmDelete', { count: archive.entry_count }))) return
  await messageArchivesApi.delete(archive.id, true)
  selectedArchive.value = null
  await loadArchives()
}

async function runIntegrityCheck() {
  if (!auth.isAdmin) return
  const { data } = await messageArchivesApi.integrityCheck()
  integrityResult.value = data
  await loadArchives()
}

async function exportArchive(format) {
  if (!auth.isAdmin) return
  if (!currentArchiveId.value) return
  const { data } = await messageArchivesApi.export(currentArchiveId.value, format)
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = `${currentArchiveId.value}.${format === 'csv' ? 'csv' : 'jsonl'}`
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(loadArchives)
</script>
