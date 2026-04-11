<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const label           = computed(() => (props.config.label           as string) ?? '')
const url             = computed(() => (props.config.url             as string) ?? '')
const streamType      = computed(() => (props.config.streamType      as string) ?? 'mjpeg')
const authType        = computed(() => (props.config.authType        as string) ?? 'none')
const username        = computed(() => (props.config.username        as string) ?? '')
const password        = computed(() => (props.config.password        as string) ?? '')
const apiKeyParam     = computed(() => (props.config.apiKeyParam     as string) ?? 'token')
const apiKeyValue     = computed(() => (props.config.apiKeyValue     as string) ?? '')
const refreshInterval = computed(() => (props.config.refreshInterval as number) ?? 5)
const aspectRatio     = computed(() => (props.config.aspectRatio     as string) ?? '16/9')
const objectFit       = computed(() => (props.config.objectFit       as string) ?? 'contain')

/** Baut die finale Stream-URL inkl. Authentifizierung */
const streamUrl = computed(() => {
  if (!url.value) return ''
  let base = url.value.trim()

  if (authType.value === 'basic' && username.value) {
    try {
      const u = new URL(base)
      u.username = encodeURIComponent(username.value)
      u.password = encodeURIComponent(password.value)
      base = u.toString()
    } catch { /* ungültige URL – unverändert */ }
  } else if (authType.value === 'apikey' && apiKeyParam.value && apiKeyValue.value) {
    try {
      const u = new URL(base)
      u.searchParams.set(apiKeyParam.value, apiKeyValue.value)
      base = u.toString()
    } catch { /* ungültige URL – unverändert */ }
  }

  return base
})

// ── Snapshot-Refresh ────────────────────────────────────────────────────────
const cacheBuster = ref(Date.now())
let refreshTimer: ReturnType<typeof setInterval> | null = null

function startRefresh() {
  stopRefresh()
  if (streamType.value !== 'snapshot' || props.editorMode) return
  const ms = Math.max(1, refreshInterval.value) * 1000
  refreshTimer = setInterval(() => { cacheBuster.value = Date.now() }, ms)
}

function stopRefresh() {
  if (refreshTimer !== null) { clearInterval(refreshTimer); refreshTimer = null }
}

const snapshotUrl = computed(() => {
  if (!streamUrl.value) return ''
  try {
    const u = new URL(streamUrl.value)
    u.searchParams.set('_t', String(cacheBuster.value))
    return u.toString()
  } catch {
    const sep = streamUrl.value.includes('?') ? '&' : '?'
    return streamUrl.value + sep + '_t=' + cacheBuster.value
  }
})

onMounted(startRefresh)
onUnmounted(stopRefresh)
watch([streamType, refreshInterval], startRefresh)

// ── Reload-Mechanismus ──────────────────────────────────────────────────────
// Statt den Stream zu ersetzen wird nur ein nicht-blockierendes Overlay gezeigt.
// Bei Fehler: nach 10 s automatisch neu laden (MJPEG-Streams können kurz unterbrechen).
const hasError  = ref(false)
const reloadKey = ref(0)  // Ändert sich → img/video neu gerendert
let retryTimer: ReturnType<typeof setTimeout> | null = null

function clearRetry() {
  if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null }
}

function onStreamError() {
  hasError.value = true
  clearRetry()
  retryTimer = setTimeout(() => reload(), 10_000)
}

function onStreamLoad() {
  hasError.value = false
  clearRetry()
}

function reload() {
  clearRetry()
  hasError.value = false
  cacheBuster.value = Date.now()
  reloadKey.value++
}

onUnmounted(clearRetry)
watch(streamUrl, () => { hasError.value = false; clearRetry(); reloadKey.value++ })

// ── Seitenverhältnis ────────────────────────────────────────────────────────
const containerStyle = computed((): Record<string, string> => {
  const fit = objectFit.value
  if (aspectRatio.value === 'free') return { objectFit: fit }
  return { aspectRatio: aspectRatio.value, objectFit: fit }
})

// Aktuell verwendete Src
const imgSrc = computed(() =>
  streamType.value === 'snapshot' ? snapshotUrl.value : streamUrl.value
)
</script>

<template>
  <div class="h-full w-full flex flex-col overflow-hidden bg-black rounded relative">

    <!-- Label -->
    <div
      v-if="label"
      class="shrink-0 px-2 py-1 text-xs text-gray-300 bg-gray-900/80 truncate z-10"
    >
      {{ label }}
    </div>

    <!-- Editor-Platzhalter (kein URL konfiguriert) -->
    <div
      v-if="editorMode && !url"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-2"
    >
      <span class="text-4xl">📷</span>
      <span class="text-xs">Kamera-URL konfigurieren</span>
    </div>

    <!-- Kein URL im Live-Modus -->
    <div
      v-else-if="!url"
      class="flex-1 flex items-center justify-center text-gray-600 text-xs"
    >
      Keine URL konfiguriert
    </div>

    <!-- MJPEG / Snapshot -->
    <div
      v-else-if="streamType === 'mjpeg' || streamType === 'snapshot'"
      class="flex-1 flex items-center justify-center overflow-hidden"
    >
      <img
        :key="reloadKey"
        :src="imgSrc"
        :style="containerStyle"
        class="max-h-full max-w-full"
        alt="Kamera"
        @error="onStreamError"
        @load="onStreamLoad"
      />
    </div>

    <!-- HLS / native Video -->
    <div
      v-else-if="streamType === 'hls'"
      class="flex-1 flex items-center justify-center overflow-hidden"
    >
      <video
        :key="reloadKey"
        :src="streamUrl"
        :style="containerStyle"
        class="max-h-full max-w-full"
        autoplay
        muted
        playsinline
        @error="onStreamError"
        @loadeddata="onStreamLoad"
      />
    </div>

    <!-- Fehler-Overlay (nicht-blockierend: liegt über dem Stream, ersetzt ihn nicht) -->
    <div
      v-if="hasError && url"
      class="absolute inset-0 flex flex-col items-center justify-center bg-black/70 gap-2 z-20"
    >
      <span class="text-2xl">⚠️</span>
      <span class="text-xs text-red-400 text-center px-4">Stream nicht erreichbar</span>
      <span class="text-xs text-gray-500 text-center px-4">Automatischer Neuversuch in 10 s</span>
      <button
        class="mt-1 px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-xs text-gray-200 transition-colors"
        @click="reload"
      >
        Jetzt neu laden
      </button>
    </div>

  </div>
</template>
