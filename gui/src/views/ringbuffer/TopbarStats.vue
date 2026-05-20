<template>
  <div
    data-testid="topbar-stats"
    class="inline-flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300"
  >
    <span class="font-medium tabular-nums">{{ formattedTotal }}</span>
    <span class="text-slate-400">/</span>
    <span class="tabular-nums">{{ displayMax }}</span>
    <span class="text-slate-400">·</span>
    <span>{{ storage }}</span>
    <span v-if="formattedDiskSize" class="text-slate-400">·</span>
    <span v-if="formattedDiskSize" class="tabular-nums" data-testid="topbar-stats-disk-size">{{ formattedDiskSize }}</span>
    <span v-if="formattedRetention" class="text-slate-400">·</span>
    <span v-if="formattedRetention" class="tabular-nums" data-testid="topbar-stats-retention"
          title="Aktuelle effektive Retention im Ringbuffer">⏱ {{ formattedRetention }}</span>
    <span
      ref="helpIcon"
      data-testid="topbar-stats-help"
      class="cursor-help text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 select-none"
      tabindex="0"
      role="button"
      aria-label="Speicherverhalten anzeigen"
      @pointerenter="showTip"
      @pointerleave="hideTip"
      @focus="showTip"
      @blur="hideTip"
    >ⓘ</span>

    <Teleport to="body">
      <div
        v-if="tipOpen"
        ref="tooltip"
        data-testid="topbar-stats-tooltip"
        class="z-50 max-w-xs px-3 py-2 rounded-md bg-slate-800 text-white text-xs shadow-lg pointer-events-none"
        :style="floatingStyles"
        role="tooltip"
      >
        <p class="font-semibold mb-1">Speicherverhalten</p>
        <p>
          Der Monitor läuft im <strong>file-only</strong>-Modus mit serverseitig
          festen Limits. Sobald die maximale Anzahl Einträge oder der
          konfigurierte Speicherplatz erreicht ist, werden die ältesten
          Einträge überschrieben.
        </p>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useFloating, autoUpdate, offset, flip, shift } from '@floating-ui/vue'
import { ringbufferApi } from '@/api/client'
import { useWebSocketStore } from '@/stores/websocket'
import { formatDurationDeutsch } from '@/composables/useTimeFilterParser'

const stats = ref(null)
const helpIcon = ref(null)
const tooltip = ref(null)
const tipOpen = ref(false)
const wsStore = useWebSocketStore()
let stopAutoUpdate = null
let pollTimer = null
let wsUnsubscribe = null
let wsRefreshDebounce = null

const total = computed(() => Number(stats.value?.total ?? 0))
const maxEntries = computed(() => {
  const raw = stats.value?.max_entries
  if (raw == null) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
})
const storage = computed(() => stats.value?.storage ?? '—')

function fmt(n) {
  if (!Number.isFinite(n)) return '—'
  try {
    return new Intl.NumberFormat('de-DE').format(n)
  } catch {
    return String(n)
  }
}

const formattedTotal = computed(() => fmt(total.value))
const formattedMax = computed(() => fmt(maxEntries.value))
const displayMax = computed(() => (maxEntries.value == null ? '∞' : formattedMax.value))

const fileSizeBytes = computed(() => {
  const raw = stats.value?.file_size_bytes
  if (raw == null) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
})

function fmtBytes(n) {
  if (!Number.isFinite(n) || n < 0) return null
  if (n === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  const formatter = new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: v >= 100 || i === 0 ? 0 : 1,
    maximumFractionDigits: v >= 100 || i === 0 ? 0 : 1,
  })
  return `${formatter.format(v)} ${units[i]}`
}

const formattedDiskSize = computed(() => fmtBytes(fileSizeBytes.value))

const formattedRetention = computed(() => {
  const raw = stats.value?.effective_retention_seconds
  const seconds = Number(raw)
  if (!Number.isFinite(seconds) || seconds <= 0) return null
  return formatDurationDeutsch(seconds)
})

const { floatingStyles, update } = useFloating(helpIcon, tooltip, {
  placement: 'bottom',
  middleware: [offset(6), flip(), shift({ padding: 8 })],
})

function startAutoUpdate() {
  if (helpIcon.value && tooltip.value && !stopAutoUpdate) {
    stopAutoUpdate = autoUpdate(helpIcon.value, tooltip.value, update)
  }
}

function stopAutoUpdateFn() {
  if (stopAutoUpdate) {
    stopAutoUpdate()
    stopAutoUpdate = null
  }
}

function showTip() {
  tipOpen.value = true
  nextTick(() => startAutoUpdate())
}

function hideTip() {
  tipOpen.value = false
  stopAutoUpdateFn()
}

async function load() {
  try {
    const { data } = await ringbufferApi.stats()
    stats.value = data
  } catch {
    stats.value = null
  }
}

onMounted(() => {
  void load()
  pollTimer = setInterval(() => { void load() }, 10000)
  wsUnsubscribe = wsStore.onRingbufferEntry(() => {
    clearTimeout(wsRefreshDebounce)
    wsRefreshDebounce = setTimeout(() => { void load() }, 250)
  })
})

onUnmounted(() => {
  stopAutoUpdateFn()
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (wsRefreshDebounce) {
    clearTimeout(wsRefreshDebounce)
    wsRefreshDebounce = null
  }
  wsUnsubscribe?.()
  wsUnsubscribe = null
})

defineExpose({ reload: load })
</script>
