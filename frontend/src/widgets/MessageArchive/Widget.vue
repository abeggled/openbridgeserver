<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { messageArchives, type MessageArchiveEntry } from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'

const props = defineProps<{
  config: Record<string, unknown>
  editorMode: boolean
  readonly?: boolean
}>()

const { t } = useI18n()
const ws = useWebSocket()
const entries = ref<MessageArchiveEntry[]>([])
const loading = ref(false)
const error = ref('')
let unsubscribeWs: (() => void) | null = null

function configStringValues(...keys: string[]): string[] {
  const values: string[] = []
  for (const key of keys) {
    const value = props.config[key]
    if (Array.isArray(value)) values.push(...value.map(String).filter(Boolean))
    if (typeof value === 'string' && value.trim()) {
      values.push(...value.split(',').map(item => item.trim()).filter(Boolean))
    }
  }
  return [...new Set(values)]
}

const archiveIds = computed<string[]>(() => configStringValues('archive_ids', 'archive_id'))
const limit = computed(() => Math.max(1, Math.min(100, Number(props.config.limit ?? 25))))
const showArchive = computed(() => (props.config.show_archive as boolean | undefined) ?? true)
const showSource = computed(() => (props.config.show_source as boolean | undefined) ?? true)
const allowRead = computed(() => (props.config.allow_read as boolean | undefined) ?? true)
const allowAcknowledge = computed(() => (props.config.allow_acknowledge as boolean | undefined) ?? true)
const canRead = computed(() => !props.readonly && allowRead.value)
const canAcknowledge = computed(() => !props.readonly && allowAcknowledge.value)

function filterValues(key: 'severity' | 'status' | 'type' | 'source'): string[] {
  const pluralKeys = {
    severity: 'severities',
    status: 'statuses',
    type: 'types',
    source: 'sources',
  } as const
  return configStringValues(key, pluralKeys[key])
}

function params(): Record<string, string | number | undefined> {
  const p: Record<string, string | number | undefined> = {
    limit: limit.value,
    sort: 'desc',
  }
  if (archiveIds.value.length) p.archive_id = archiveIds.value.join(',')
  for (const key of ['severity', 'status', 'type', 'source']) {
    const values = filterValues(key as 'severity' | 'status' | 'type' | 'source')
    if (values.length) p[key] = values.join(',')
  }
  return p
}

function severityClass(severity: string): string {
  if (severity === 'critical') return 'bg-red-600'
  if (severity === 'error') return 'bg-red-500'
  if (severity === 'warning') return 'bg-amber-500'
  return 'bg-blue-500'
}

function fmt(value: string): string {
  return new Date(value).toLocaleString()
}

function statusLabel(value: string): string {
  const key = `widgets.messageArchive.statuses.${value}`
  return t(key) === key ? value : t(key)
}

function typeLabel(value: string): string {
  const key = `widgets.messageArchive.types.${value}`
  return t(key) === key ? value : t(key)
}

function severityLabel(value: string): string {
  const key = `widgets.messageArchive.severities.${value}`
  return t(key) === key ? value : t(key)
}

function matchesFilters(entry: MessageArchiveEntry): boolean {
  if (archiveIds.value.length && !archiveIds.value.includes(entry.archive_id)) return false
  for (const key of ['severity', 'status', 'type', 'source'] as const) {
    const values = filterValues(key)
    if (values.length && !values.includes(entry[key])) {
      return false
    }
  }
  return true
}

function applyLiveEntry(entry: MessageArchiveEntry) {
  const existing = entries.value.find(item => item.id === entry.id)
  const merged = existing && existing.is_read && !entry.is_read
    ? { ...entry, is_read: true, read_at: existing.read_at }
    : entry
  if (!matchesFilters(merged)) {
    entries.value = entries.value.filter(item => item.id !== merged.id)
    return
  }
  entries.value = [...entries.value.filter(item => item.id !== merged.id), merged]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, limit.value)
  error.value = ''
}

function isMessageArchiveEntry(value: unknown): value is MessageArchiveEntry {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<MessageArchiveEntry>
  return typeof candidate.id === 'string'
    && typeof candidate.archive_id === 'string'
    && typeof candidate.created_at === 'string'
    && typeof candidate.type === 'string'
    && typeof candidate.severity === 'string'
    && typeof candidate.status === 'string'
    && typeof candidate.message === 'string'
}

async function load() {
  if (props.editorMode) {
    entries.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await messageArchives.entries(params())
    entries.value = result.items
  } catch {
    error.value = t('widgets.messageArchive.loadError')
  } finally {
    loading.value = false
  }
}

async function markRead(entry: MessageArchiveEntry) {
  if (props.editorMode || !canRead.value) return
  const updated = await messageArchives.markRead(entry.archive_id, entry.id)
  entries.value = entries.value.map(item => item.id === updated.id ? updated : item)
  await load()
}

async function acknowledge(entry: MessageArchiveEntry) {
  if (props.editorMode || !canAcknowledge.value) return
  const updated = await messageArchives.acknowledge(entry.archive_id, entry.id)
  entries.value = entries.value.map(item => item.id === updated.id ? updated : item)
  await load()
}

watch(() => props.config, load, { deep: true })
onMounted(() => {
  load()
  unsubscribeWs = ws.onMessage((data) => {
    if (props.editorMode || data.action !== 'message_archive_entry') return
    if (isMessageArchiveEntry(data.entry)) applyLiveEntry(data.entry)
  })
})
onUnmounted(() => {
  unsubscribeWs?.()
  unsubscribeWs = null
})
</script>

<template>
  <div class="h-full flex flex-col overflow-hidden p-3 gap-2">
    <div class="flex items-center gap-2 shrink-0">
      <span class="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
        {{ $t('widgets.messageArchive.title') }}
      </span>
      <button
        type="button"
        class="ml-auto text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
        @click="load"
      >
        {{ $t('widgets.messageArchive.refresh') }}
      </button>
    </div>

    <div v-if="loading" class="text-xs text-gray-500 py-4">{{ $t('common.loading') }}</div>
    <div v-else-if="error" class="text-xs text-red-500 py-4">{{ error }}</div>
    <div v-else-if="!entries.length" class="text-xs text-gray-500 py-4">{{ $t('widgets.messageArchive.empty') }}</div>
    <div v-else class="min-h-0 overflow-auto space-y-2 pr-1">
      <article
        v-for="entry in entries"
        :key="entry.id"
        class="border-l-4 rounded bg-white/70 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 p-2"
        :style="{ borderLeftColor: entry.archive_color }"
      >
        <div class="flex items-start gap-2">
          <span class="mt-1 w-2 h-2 rounded-full shrink-0" :class="severityClass(entry.severity)" />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{{ entry.title || typeLabel(entry.type) }}</p>
              <span v-if="!entry.is_read" class="text-[10px] uppercase tracking-wide text-blue-600 dark:text-blue-300">{{ $t('widgets.messageArchive.unread') }}</span>
            </div>
            <p class="text-xs text-gray-600 dark:text-gray-300 break-words">{{ entry.message }}</p>
            <div class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
              <span>{{ fmt(entry.created_at) }}</span>
              <span v-if="showArchive">{{ entry.archive_name }}</span>
              <span>{{ severityLabel(entry.severity) }}</span>
              <span>{{ statusLabel(entry.status) }}</span>
              <span v-if="showSource && entry.source">{{ entry.source }}</span>
            </div>
          </div>
        </div>
        <div v-if="canRead || canAcknowledge" class="mt-2 flex gap-2 justify-end">
          <button
            v-if="canRead && !entry.is_read"
            type="button"
            class="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
            @click="markRead(entry)"
          >
            {{ $t('widgets.messageArchive.markRead') }}
          </button>
          <button
            v-if="canAcknowledge && entry.status !== 'acknowledged'"
            type="button"
            class="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
            @click="acknowledge(entry)"
          >
            {{ $t('widgets.messageArchive.acknowledge') }}
          </button>
        </div>
      </article>
    </div>
  </div>
</template>
