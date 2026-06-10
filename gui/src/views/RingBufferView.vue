<template>
  <!-- h-full + flex column so the table-wrap below can flex-1 into the
       remaining viewport space (AppLayout's <main> already provides the
       scrolling fallback if total content overflows). -->
  <div class="flex flex-col gap-5 h-full min-h-0">
    <div class="flex flex-wrap items-start gap-3 shrink-0">
      <div class="flex-1">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ $t('ringbuffer.title') }}</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ $t('ringbuffer.subtitle') }}</p>
      </div>
      <!-- Right cluster: aligns buttons + status chip + ringbuffer stats on a
           single baseline; status badge matches the button height. -->
      <div class="flex items-center gap-2">
        <button @click="showConfig = true" class="btn-secondary btn-sm" data-testid="btn-open-monitor-config">{{ $t('ringbuffer.configure') }}</button>
        <button @click="applyFilters" class="btn-secondary btn-sm" data-testid="btn-refresh-ringbuffer">{{ $t('ringbuffer.refresh') }}</button>
        <button
          v-if="!paused"
          @click="pauseLive"
          class="btn-secondary btn-sm"
          data-testid="btn-live-pause"
        >
          {{ $t('ringbuffer.pause') }}
        </button>
        <button
          v-else
          @click="resumeLive"
          class="btn-secondary btn-sm"
          data-testid="btn-live-resume"
        >
          {{ $t('ringbuffer.resume') }}
        </button>
        <!-- Single, consolidated status indicator: WS-connection + pause-state.
             Same padding/height as btn-sm so the chip sits on the button
             baseline (gap-2 aligns horizontally, py-1.5 vertically matches). -->
        <span :class="['inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium', statusBadgeClass]"
          data-testid="status-badge"
          :title="statusBadgeTitle">
          <span :class="['w-1.5 h-1.5 rounded-full', statusDotClass]" />
          {{ statusBadgeText }}
        </span>
        <TopbarStats />
      </div>
    </div>

    <!-- Sticky filter topbar (#435) — drag/toggle/remove set chips, TimeFilterPopover (#432) in the left slot. -->
    <div class="sticky top-0 z-20 -mx-5 px-5 py-2 bg-surface-900/95 backdrop-blur-sm border-b border-slate-200 dark:border-slate-700/60">
      <TopbarFilterChips
        ref="topbarChipsRef"
        data-testid="ringbuffer-topbar-chips"
        @edit-set="onEditSet"
        @new-set="onNewSet"
        @changed="onTopbarChanged"
        @export="openExportDialog"
      >
        <template #time-filter-slot>
          <TimeFilterPopover v-model="timeFilter" @update:modelValue="onTimeFilterChanged" />
        </template>
      </TopbarFilterChips>
    </div>

    <!-- Soft-modal filter editor (#436) — replaces the stub from #435 -->
    <FilterEditor
      v-model="showFilterEditor"
      :set-id="editorTargetId"
      @saved="onFilterEditorSaved"
      @deleted="onFilterEditorDeleted"
    />

    <div
      v-if="recoveryNotice"
      class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300"
      data-testid="ringbuffer-recovery-notice"
    >
      {{ recoveryNotice }}
    </div>

    <!-- Soft-modal CSV/TSV export dialog (#427) -->
    <ExportDialog
      v-model="showExportDialog"
      :set-ids="activeTopbarSetIds()"
      :time="timeFilterToPayload(timeFilter)"
    />

    <div class="card overflow-hidden flex-1 min-h-0 flex flex-col">
      <div v-if="loading" class="flex justify-center py-12"><Spinner size="lg" /></div>
      <div v-else-if="listError" class="px-4 py-6 text-sm text-red-500" data-testid="ringbuffer-error">{{ listError }}</div>
      <div v-else-if="!entries.length" class="text-center text-slate-500 text-sm py-12" data-testid="ringbuffer-empty">{{ $t('ringbuffer.noEntries') }}</div>
      <div v-else class="table-wrap flex-1 min-h-0 overflow-y-auto" ref="tableWrapRef" data-testid="ringbuffer-table-wrap">
        <table class="table">
          <thead class="sticky top-0">
            <tr>
              <th>{{ $t('ringbuffer.colTimestamp') }}</th>
              <th>{{ $t('ringbuffer.colObject') }}</th>
              <th>{{ $t('ringbuffer.colValue') }}</th>
              <th>{{ $t('ringbuffer.colPrevValue') }}</th>
              <th>{{ $t('ringbuffer.colQuality') }}</th>
              <th>{{ $t('ringbuffer.colAdapter') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(e, i) in entries"
              :key="i"
              data-testid="ringbuffer-entry"
              :data-dp="e.datapoint_id"
              :class="getRowStyle(e.matched_set_ids) ? 'ringbuffer-row-matched' : null"
              :style="getRowStyle(e.matched_set_ids)"
              :title="e.match_title"
            >
              <td class="font-mono text-xs text-slate-400 whitespace-nowrap">{{ fmtDateTime(e.ts) }}</td>
              <td class="text-sm">
                <RouterLink :to="`/datapoints/${e.datapoint_id}`" class="text-blue-400 hover:underline font-mono text-xs">
                  {{ e.name ?? e.datapoint_id?.slice(0, 8) }}
                </RouterLink>
              </td>
              <td class="font-mono text-sm text-blue-500 dark:text-blue-300">{{ e.new_value }}<span v-if="e.unit" class="text-slate-500 ml-1 text-xs">{{ e.unit }}</span></td>
              <td class="font-mono text-sm text-slate-500">{{ e.old_value ?? '-' }}<span v-if="e.unit && e.old_value !== null && e.old_value !== undefined" class="text-slate-500 ml-1 text-xs">{{ e.unit }}</span></td>
              <td><Badge :variant="e.quality === 'good' ? 'success' : 'warning'" size="xs" dot>{{ qualityLabel(e.quality) }}</Badge></td>
              <td class="text-xs text-slate-500">{{ e.source_adapter ?? '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <MonitorConfigModal v-model="showConfig" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ringbufferApi } from '@/api/client'
import { useTz } from '@/composables/useTz'
import { useSetColors } from '@/composables/useSetColors'
import { useLiveQueue } from '@/composables/useLiveQueue'
import { timeFilterToPayload, entryInTimeWindow } from '@/composables/useTimeFilterPayload'
import { matchedSetIds } from '@/composables/useClientSideMatch'
import { useWebSocketStore } from '@/stores/websocket'
import Badge from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'
import TimeFilterPopover from '@/components/ui/TimeFilterPopover.vue'
import TopbarFilterChips from '@/views/ringbuffer/TopbarFilterChips.vue'
import TopbarStats from '@/views/ringbuffer/TopbarStats.vue'
import FilterEditor from '@/views/ringbuffer/FilterEditor.vue'
import ExportDialog from '@/views/ringbuffer/ExportDialog.vue'
import MonitorConfigModal from '@/views/ringbuffer/MonitorConfigModal.vue'

const DEFAULT_QUERY_LIMIT = 500

const { t } = useI18n()
const { fmtDateTime } = useTz()
const wsStore = useWebSocketStore()
const { getRowStyle, setSets, sets: topbarSetsRef } = useSetColors()

function rowMatchTitle(matchedIds) {
  if (!Array.isArray(matchedIds) || matchedIds.length === 0) return null
  const names = []
  for (const id of matchedIds) {
    const set = topbarSetsRef.value.get(id)
    if (set?.name) names.push(set.name)
  }
  return names.length ? t('ringbuffer.rowMatchTitle', { names: names.join(', ') }) : null
}

function withRowMatchTitle(entry) {
  return {
    ...entry,
    match_title: rowMatchTitle(entry?.matched_set_ids),
  }
}

const entries = ref([])
const loading = ref(false)
const listError = ref('')
const showConfig = ref(false)
const showFilterEditor = ref(false)
const showExportDialog = ref(false)
const editorTargetId = ref(null)
const topbarChipsRef = ref(null)
const recoveryNotice = ref('')
let recoveryNoticeRefreshPromise = null
let lastRecoveryNoticeRefreshAt = 0

function onEditSet(id) {
  editorTargetId.value = id
  showFilterEditor.value = true
}

function onNewSet() {
  editorTargetId.value = null
  showFilterEditor.value = true
}

function openExportDialog() {
  showExportDialog.value = true
}

async function onFilterEditorDeleted() {
  // After a set has been deleted: drop it from the local cache, the topbar
  // chip strip (where it may have been pinned), the color cache, and re-run
  // the table query so the matched-set colour painting falls back to the
  // remaining sets only.
  await loadFiltersets()
  topbarChipsRef.value?.reload?.()
  await load()
}

async function onFilterEditorSaved() {
  // Re-fetch filtersets so the topbar chips reflect any changes (rename,
  // color, topbar membership, etc.). Also refresh the local select-list and
  // re-run the table query so the filter change becomes visible without a
  // page reload (#36 follow-up bug fix).
  await loadFiltersets()
  topbarChipsRef.value?.reload?.()
  await load()
}

async function onTopbarChanged() {
  // Topbar chip toggle / drag-reorder happened — refresh the local filterset
  // cache (which also feeds the row-colour map) and re-run the query so the
  // table reflects the new topbar set composition.
  await loadFiltersets()
  await load()
}

// TimeFilterPopover state (#432). See useTimeFilterPayload for the shape.
const timeFilter = ref(null)

async function onTimeFilterChanged() {
  // Re-run the query when the user applies a new time filter.
  await load()
}

const tableWrapRef = ref(null)
const filtersets = ref([])
let liveIngressSeq = 0

const { paused, queuedCount, enqueue: enqueueLive, pause: pauseLive, resume: resumeLive, clear: clearLiveQueue, dispose: disposeLiveQueue } =
  useLiveQueue(entries, {
    maxEntries: DEFAULT_QUERY_LIMIT,
    onFlush: async () => {
      await nextTick()
      if (tableWrapRef.value) tableWrapRef.value.scrollTop = 0
    },
  })

function markAndEnqueueLive(entry) {
  liveIngressSeq += 1
  enqueueLive(withRowMatchTitle(entry))
}

function entryIdentity(entry) {
  return [
    entry?.datapoint_id ?? '',
    entry?.ts ?? '',
    entry?.source_adapter ?? '',
    JSON.stringify(entry?.new_value ?? null),
    JSON.stringify(entry?.old_value ?? null),
  ].join('|')
}

function mergeEntriesKeepingLiveFirst(liveFirst, loaded) {
  const merged = []
  const seen = new Set()
  for (const entry of [...liveFirst, ...loaded]) {
    const key = entryIdentity(entry)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(entry)
    if (merged.length >= DEFAULT_QUERY_LIMIT) break
  }
  return merged
}

const wsConnected = computed(() => wsStore.connected)

const statusBadgeText = computed(() => {
  if (!wsConnected.value) return t('sidebar.offline')
  if (paused.value) return t('ringbuffer.paused', { n: queuedCount.value })
  return t('sidebar.live')
})

const statusBadgeClass = computed(() => {
  if (!wsConnected.value) return 'bg-slate-200/50 dark:bg-slate-700/50 text-slate-500'
  if (paused.value) return 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
  return 'bg-teal-500/15 text-teal-600 dark:text-teal-400'
})

const statusDotClass = computed(() => {
  if (!wsConnected.value) return 'bg-slate-600'
  if (paused.value) return 'bg-amber-500'
  return 'bg-teal-400 animate-pulse'
})

const statusBadgeTitle = computed(() => {
  if (!wsConnected.value) return t('ringbuffer.offlineTitle')
  if (paused.value) return t('ringbuffer.pausedTitle')
  return t('ringbuffer.liveTitle')
})

let unregisterRb = null

function extractErrorMessage(error, fallback) {
  return error?.response?.data?.detail || error?.message || fallback
}

async function loadFiltersets() {
  try {
    const { data } = await ringbufferApi.listFiltersets()
    filtersets.value = Array.isArray(data) ? data : []
    // Keep the row-colour cache in sync with the topbar state (#437).
    setSets(filtersets.value)
  } catch {
    filtersets.value = []
    setSets([])
  }
}

async function loadRecoveryNotice() {
  try {
    const { data } = await ringbufferApi.stats()
    if (!data?.last_recovery_at) {
      recoveryNotice.value = ''
      return
    }
    recoveryNotice.value = t('ringbuffer.recoveryNotice', {
      ts: fmtDateTime(data.last_recovery_at),
      n: Number(data.last_recovery_file_count ?? 0),
    })
  } catch {
    recoveryNotice.value = ''
  }
}

function refreshRecoveryNoticeSoon() {
  if (recoveryNotice.value) return
  if (recoveryNoticeRefreshPromise) return
  const now = Date.now()
  if (lastRecoveryNoticeRefreshAt && now - lastRecoveryNoticeRefreshAt < 5000) return
  lastRecoveryNoticeRefreshAt = now
  recoveryNoticeRefreshPromise = loadRecoveryNotice().finally(() => {
    recoveryNoticeRefreshPromise = null
  })
}

function buildQueryV2() {
  const payload = {
    filters: {},
    sort: { field: 'ts', order: 'desc' },
    pagination: { limit: DEFAULT_QUERY_LIMIT, offset: 0 },
  }
  const time = timeFilterToPayload(timeFilter.value)
  if (time) payload.filters.time = time
  return payload
}

function onLiveEntry(entry) {
  refreshRecoveryNoticeSoon()

  // First gate: honor the active TimeFilterPopover. Entries outside the
  // resolved window are dropped — a fixed past window or a point ± span
  // window in the past therefore produces a static table, matching user
  // expectation. Open-ended/sliding windows (e.g. `from=-1h` with no
  // `to`) still keep current entries flowing.
  if (!entryInTimeWindow(entry, timeFilter.value)) return

  // Three branches:
  //   1. No active topbar sets → unfiltered live feed (legacy behaviour).
  //   2. Entry already carries matched_set_ids from the server → trust them
  //      (future-compatible path for when the WS push starts including the
  //      match annotation; the row-color spec exercises this case).
  //   3. Active sets present and entry has no preset → client-side match.
  //      Empty FilterCriteria match nothing (#36 semantics, see useClientSideMatch).
  //      Hierarchy sets are matched from WS metadata; entries that match none
  //      of the active sets are dropped.
  const activeSets = filtersets.value.filter((s) => s.topbar_active && s.is_active !== false)
  const presetMatched = Array.isArray(entry?.matched_set_ids) ? entry.matched_set_ids : null

  if (activeSets.length === 0) {
    markAndEnqueueLive({ ...entry, matched_set_ids: presetMatched ?? [] })
    return
  }
  if (presetMatched && presetMatched.length > 0) {
    markAndEnqueueLive({ ...entry, matched_set_ids: presetMatched })
    return
  }
  const ids = matchedSetIds(entry, activeSets)
  if (ids.length === 0) return
  markAndEnqueueLive({ ...entry, matched_set_ids: ids })
}

async function applyFilters() {
  clearLiveQueue()
  await load()
}

onMounted(async () => {
  // Register the live handler BEFORE the initial load. The WebSocket
  // connects faster than the loadFiltersets()/load() round-trips, so a
  // ringbuffer_entry push arriving in that gap would otherwise be dropped
  // (the server does not replay events).
  unregisterRb = wsStore.onRingbufferEntry(onLiveEntry)
  // Load filtersets first so the topbar-colour cache is populated before
  // load() picks between queryV2 and queryMultiFiltersets (#437). /stats
  // is owned by TopbarStats / the config modal — not fetched here.
  await loadFiltersets()
  await load()
})

onUnmounted(() => {
  unregisterRb?.()
  disposeLiveQueue()
})

function activeTopbarSetIds() {
  // Walk the cached topbar-set map in ascending topbar_order. The order is
  // also what the chip strip displays, so the first-match-wins tie-break in
  // useSetColors() lines up with the visual order.
  return Array.from(topbarSetsRef.value.values())
    .sort((a, b) => (a.topbar_order ?? 0) - (b.topbar_order ?? 0))
    .map((s) => s.id)
}

async function load() {
  loading.value = true
  listError.value = ''
  const liveSeqAtLoadStart = liveIngressSeq
  try {
    const setIds = activeTopbarSetIds()
    let data
    if (setIds.length > 0) {
      // Multi-filterset OR-union query (#431); each entry carries
      // matched_set_ids so the table can colour-code rows by matched set.
      const body = { set_ids: setIds }
      const time = timeFilterToPayload(timeFilter.value)
      if (time) body.time = time
      const resp = await ringbufferApi.queryMultiFiltersets(body)
      data = resp.data
    } else {
      // Default path — no topbar sets active, keep the single-query flow.
      const resp = await ringbufferApi.queryV2(buildQueryV2())
      data = resp.data
    }
    const loadedEntries = Array.isArray(data) ? data.map(withRowMatchTitle) : []
    const liveArrivedDuringLoad = liveIngressSeq !== liveSeqAtLoadStart
    entries.value = liveArrivedDuringLoad
      ? mergeEntriesKeepingLiveFirst(entries.value, loadedEntries)
      : loadedEntries
    await nextTick()
    if (!paused.value && tableWrapRef.value) tableWrapRef.value.scrollTop = 0
  } catch (error) {
    entries.value = []
    listError.value = extractErrorMessage(error, t('ringbuffer.queryFailed'))
  } finally {
    await loadRecoveryNotice()
    loading.value = false
  }
}

function qualityLabel(q) {
  return q === 'good' ? t('datapoints.quality.good') : q === 'bad' ? t('datapoints.quality.bad') : q === 'uncertain' ? t('datapoints.quality.uncertain') : q
}
</script>
