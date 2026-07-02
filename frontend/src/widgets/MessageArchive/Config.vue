<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { messageArchives, type MessageArchiveEntry, type MessageArchiveOut } from '@/api/client'

interface Cfg {
  archive_ids: string[]
  limit: number
  severity: string[]
  status: string[]
  type: string[]
  source: string[]
  show_archive: boolean
  show_source: boolean
  allow_read: boolean
  allow_acknowledge: boolean
}

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: Record<string, unknown>): void }>()

const { t } = useI18n()
const archives = ref<MessageArchiveOut[]>([])
const sourceOptions = ref<string[]>([])
const loadError = ref('')
const MESSAGE_TYPES = ['system', 'security', 'notification', 'automation', 'adapter', 'diagnostic']
const MESSAGE_SEVERITIES = ['info', 'success', 'warning', 'error', 'critical']
const MESSAGE_STATUSES = ['new', 'open', 'acknowledged', 'closed']

function listFromModel(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (typeof value === 'string' && value.trim()) {
    return value.split(',').map(item => item.trim()).filter(Boolean)
  }
  return []
}

const cfg = reactive<Cfg>({
  archive_ids: Array.isArray(props.modelValue.archive_ids) ? props.modelValue.archive_ids as string[] : [],
  limit: (props.modelValue.limit as number | undefined) ?? 25,
  severity: listFromModel(props.modelValue.severity),
  status: listFromModel(props.modelValue.status),
  type: listFromModel(props.modelValue.type),
  source: listFromModel(props.modelValue.source),
  show_archive: (props.modelValue.show_archive as boolean | undefined) ?? true,
  show_source: (props.modelValue.show_source as boolean | undefined) ?? true,
  allow_read: (props.modelValue.allow_read as boolean | undefined) ?? true,
  allow_acknowledge: (props.modelValue.allow_acknowledge as boolean | undefined) ?? true,
})

watch(cfg, () => {
  emit('update:modelValue', {
    archive_ids: cfg.archive_ids,
    limit: cfg.limit,
    severity: cfg.severity.length ? cfg.severity : undefined,
    status: cfg.status.length ? cfg.status : undefined,
    type: cfg.type.length ? cfg.type : undefined,
    source: cfg.source.length ? cfg.source : undefined,
    show_archive: cfg.show_archive,
    show_source: cfg.show_source,
    allow_read: cfg.allow_read,
    allow_acknowledge: cfg.allow_acknowledge,
  })
}, { deep: true })

const allSourceOptions = computed(() => {
  const values = new Set([...sourceOptions.value, ...cfg.source])
  return Array.from(values).sort((a, b) => a.localeCompare(b))
})

function toggleList(key: 'archive_ids' | 'severity' | 'status' | 'type' | 'source', value: string) {
  const current = cfg[key]
  if (current.includes(value)) {
    cfg[key] = current.filter(item => item !== value) as never
  } else {
    cfg[key] = [...current, value] as never
  }
}

function clearList(key: 'archive_ids' | 'severity' | 'status' | 'type' | 'source') {
  cfg[key] = [] as never
}

function archiveLabel(id: string): string {
  return archives.value.find(archive => archive.id === id)?.name ?? id
}

function typeLabel(value: string): string {
  return t(`widgets.messageArchive.types.${value}`)
}

function severityLabel(value: string): string {
  return t(`widgets.messageArchive.severities.${value}`)
}

function statusLabel(value: string): string {
  return t(`widgets.messageArchive.statuses.${value}`)
}

function selectedLabel(values: string[], allLabel: string, labelFn: (value: string) => string): string {
  if (!values.length) return allLabel
  if (values.length <= 2) return values.map(labelFn).join(', ')
  return t('widgets.messageArchive.selectedCount', { count: values.length })
}

async function loadArchives() {
  try {
    archives.value = await messageArchives.list()
  } catch {
    loadError.value = t('widgets.messageArchive.loadArchivesError')
  }
}

async function loadSourceOptions() {
  try {
    const params: Record<string, string | number | undefined> = { limit: 1000, sort: 'desc' }
    if (cfg.archive_ids.length) params.archive_id = cfg.archive_ids.join(',')
    const result = await messageArchives.entries(params)
    sourceOptions.value = Array.from(new Set(
      (result.items as MessageArchiveEntry[])
        .map(entry => entry.source)
        .filter((source): source is string => !!source),
    )).sort((a, b) => a.localeCompare(b))
  } catch {
    sourceOptions.value = []
  }
}

watch(() => cfg.archive_ids, loadSourceOptions, { deep: true })

onMounted(async () => {
  await loadArchives()
  await loadSourceOptions()
})
</script>

<template>
  <div class="space-y-4 text-sm">
    <div>
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">{{ $t('widgets.messageArchive.archives') }}</p>
      <p v-if="loadError" class="text-xs text-red-400">{{ loadError }}</p>
      <details v-else class="relative">
        <summary class="mt-1 w-full cursor-pointer list-none truncate rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-gray-100">
          {{ selectedLabel(cfg.archive_ids, $t('widgets.messageArchive.allArchives'), archiveLabel) }}
        </summary>
        <div class="absolute z-20 mt-1 max-h-52 w-full overflow-auto rounded border border-gray-700 bg-gray-900 p-2 shadow-lg">
          <button type="button" class="mb-2 text-xs text-blue-300" @click="clearList('archive_ids')">{{ $t('widgets.messageArchive.clearFilter') }}</button>
          <label v-for="archive in archives" :key="archive.id" class="flex items-center gap-2 py-1 text-xs text-gray-300">
            <input type="checkbox" :checked="cfg.archive_ids.includes(archive.id)" @change="toggleList('archive_ids', archive.id)" />
            <span class="w-2 h-2 rounded-full" :style="{ backgroundColor: archive.color }" />
            <span class="truncate">{{ archive.name }}</span>
          </label>
        </div>
      </details>
    </div>

    <label class="block text-xs text-gray-400">
      {{ $t('widgets.messageArchive.limit') }}
      <input v-model.number="cfg.limit" type="number" min="1" max="100" class="mt-1 w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100" />
    </label>

    <div class="grid grid-cols-2 gap-2">
      <label class="block text-xs text-gray-400">
        {{ $t('widgets.messageArchive.severity') }}
        <details class="relative">
          <summary class="mt-1 w-full cursor-pointer list-none truncate rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-gray-100">
            {{ selectedLabel(cfg.severity, $t('widgets.messageArchive.allSeverities'), severityLabel) }}
          </summary>
          <div class="absolute z-20 mt-1 max-h-52 w-full overflow-auto rounded border border-gray-700 bg-gray-900 p-2 shadow-lg">
            <button type="button" class="mb-2 text-xs text-blue-300" @click="clearList('severity')">{{ $t('widgets.messageArchive.clearFilter') }}</button>
            <label v-for="severity in MESSAGE_SEVERITIES" :key="severity" class="flex items-center gap-2 py-1 text-xs text-gray-300">
              <input type="checkbox" :checked="cfg.severity.includes(severity)" @change="toggleList('severity', severity)" />
              <span>{{ severityLabel(severity) }}</span>
            </label>
          </div>
        </details>
      </label>
      <label class="block text-xs text-gray-400">
        {{ $t('widgets.messageArchive.status') }}
        <details class="relative">
          <summary class="mt-1 w-full cursor-pointer list-none truncate rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-gray-100">
            {{ selectedLabel(cfg.status, $t('widgets.messageArchive.allStatuses'), statusLabel) }}
          </summary>
          <div class="absolute z-20 mt-1 max-h-52 w-full overflow-auto rounded border border-gray-700 bg-gray-900 p-2 shadow-lg">
            <button type="button" class="mb-2 text-xs text-blue-300" @click="clearList('status')">{{ $t('widgets.messageArchive.clearFilter') }}</button>
            <label v-for="status in MESSAGE_STATUSES" :key="status" class="flex items-center gap-2 py-1 text-xs text-gray-300">
              <input type="checkbox" :checked="cfg.status.includes(status)" @change="toggleList('status', status)" />
              <span>{{ statusLabel(status) }}</span>
            </label>
          </div>
        </details>
      </label>
      <label class="block text-xs text-gray-400">
        {{ $t('widgets.messageArchive.type') }}
        <details class="relative">
          <summary class="mt-1 w-full cursor-pointer list-none truncate rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-gray-100">
            {{ selectedLabel(cfg.type, $t('widgets.messageArchive.allTypes'), typeLabel) }}
          </summary>
          <div class="absolute z-20 mt-1 max-h-52 w-full overflow-auto rounded border border-gray-700 bg-gray-900 p-2 shadow-lg">
            <button type="button" class="mb-2 text-xs text-blue-300" @click="clearList('type')">{{ $t('widgets.messageArchive.clearFilter') }}</button>
            <label v-for="type in MESSAGE_TYPES" :key="type" class="flex items-center gap-2 py-1 text-xs text-gray-300">
              <input type="checkbox" :checked="cfg.type.includes(type)" @change="toggleList('type', type)" />
              <span>{{ typeLabel(type) }}</span>
            </label>
          </div>
        </details>
      </label>
      <label class="block text-xs text-gray-400">
        {{ $t('widgets.messageArchive.source') }}
        <details class="relative">
          <summary class="mt-1 w-full cursor-pointer list-none truncate rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-gray-100">
            {{ selectedLabel(cfg.source, $t('widgets.messageArchive.allSources'), value => value) }}
          </summary>
          <div class="absolute z-20 mt-1 max-h-52 w-full overflow-auto rounded border border-gray-700 bg-gray-900 p-2 shadow-lg">
            <button type="button" class="mb-2 text-xs text-blue-300" @click="clearList('source')">{{ $t('widgets.messageArchive.clearFilter') }}</button>
            <p v-if="!allSourceOptions.length" class="py-1 text-xs text-gray-500">{{ $t('widgets.messageArchive.noSources') }}</p>
            <label v-for="source in allSourceOptions" :key="source" class="flex items-center gap-2 py-1 text-xs text-gray-300">
              <input type="checkbox" :checked="cfg.source.includes(source)" @change="toggleList('source', source)" />
              <span class="truncate">{{ source }}</span>
            </label>
          </div>
        </details>
      </label>
    </div>

    <div class="space-y-2">
      <label class="flex items-center gap-2 text-xs text-gray-300">
        <input v-model="cfg.show_archive" type="checkbox" />
        {{ $t('widgets.messageArchive.showArchive') }}
      </label>
      <label class="flex items-center gap-2 text-xs text-gray-300">
        <input v-model="cfg.show_source" type="checkbox" />
        {{ $t('widgets.messageArchive.showSource') }}
      </label>
      <label class="flex items-center gap-2 text-xs text-gray-300">
        <input v-model="cfg.allow_read" type="checkbox" />
        {{ $t('widgets.messageArchive.allowRead') }}
      </label>
      <label class="flex items-center gap-2 text-xs text-gray-300">
        <input v-model="cfg.allow_acknowledge" type="checkbox" />
        {{ $t('widgets.messageArchive.allowAcknowledge') }}
      </label>
    </div>
  </div>
</template>
