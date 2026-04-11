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

const label        = computed(() => (props.config.label        as string) ?? '')
const url          = computed(() => (props.config.url          as string) ?? '')
const streamType   = computed(() => (props.config.streamType   as string) ?? 'mjpeg')
const authType     = computed(() => (props.config.authType     as string) ?? 'none')
const username     = computed(() => (props.config.username     as string) ?? '')
const password     = computed(() => (props.config.password     as string) ?? '')
const apiKeyParam  = computed(() => (props.config.apiKeyParam  as string) ?? 'token')
const apiKeyValue  = computed(() => (props.config.apiKeyValue  as string) ?? '')
const refreshInterval = computed(() => (props.config.refreshInterval as number) ?? 5)
const aspectRatio  = computed(() => (props.config.aspectRatio  as string) ?? '16/9')
const objectFit    = computed(() => (props.config.objectFit    as string) ?? 'contain')

/** Baut die finale Stream-URL inkl. Authentifizierung */
const streamUrl = computed(() => {
  if (!url.value) return ''

  let base = url.value.trim()

  if (authType.value === 'basic' && username.value) {
    // Credentials in URL einbetten: http://user:pass@host/path
    try {
      const u = new URL(base)
      u.username = encodeURIComponent(username.value)
      u.password = encodeURIComponent(password.value)
      base = u.toString()
    } catch {
      // Ungültige URL – unverändert weitergeben
    }
  } else if (authType.value === 'apikey' && apiKeyParam.value && apiKeyValue.value) {
    try {
      const u = new URL(base)
      u.searchParams.set(apiKeyParam.value, apiKeyValue.value)
      base = u.toString()
    } catch {
      // Ungültige URL – unverändert weitergeben
    }
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
  if (refreshTimer !== null) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

const snapshotUrl = computed(() => {
  if (!streamUrl.value) return ''
  try {
    const u = new URL(streamUrl.value)
    u.searchParams.set('_t', String(cacheBuster.value))
    return u.toString()
  } catch {
    return streamUrl.value + (streamUrl.value.includes('?') ? '&' : '?') + '_t=' + cacheBuster.value
  }
})

onMounted(startRefresh)
onUnmounted(stopRefresh)
watch([streamType, refreshInterval], startRefresh)

// ── Error-Handling ──────────────────────────────────────────────────────────
const hasError = ref(false)

function onImgError() { hasError.value = true }
function onImgLoad()  { hasError.value = false }

watch(streamUrl, () => { hasError.value = false })

// ── Seitenverhältnis ────────────────────────────────────────────────────────
const containerStyle = computed(() => {
  if (aspectRatio.value === 'free') return {}
  return { aspectRatio: aspectRatio.value }
})
</script>

<template>
  <div class="h-full w-full flex flex-col overflow-hidden bg-black rounded">
    <!-- Label -->
    <div
      v-if="label"
      class="shrink-0 px-2 py-1 text-xs text-gray-300 bg-gray-900/80 truncate"
    >
      {{ label }}
    </div>

    <!-- Editor-Platzhalter -->
    <div
      v-if="editorMode && !url"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-2"
    >
      <span class="text-4xl">📷</span>
      <span class="text-xs">Kamera-URL konfigurieren</span>
    </div>

    <!-- Fehler -->
    <div
      v-else-if="hasError"
      class="flex-1 flex flex-col items-center justify-center text-red-400 gap-2"
    >
      <span class="text-3xl">⚠️</span>
      <span class="text-xs text-center px-2">Stream nicht erreichbar</span>
    </div>

    <!-- MJPEG / Snapshot -->
    <div
      v-else-if="streamType === 'mjpeg' || streamType === 'snapshot'"
      class="flex-1 flex items-center justify-center overflow-hidden"
    >
      <img
        :src="streamType === 'snapshot' ? snapshotUrl : streamUrl"
        :style="{ objectFit: objectFit, ...containerStyle }"
        class="max-h-full max-w-full"
        alt="Kamera"
        @error="onImgError"
        @load="onImgLoad"
      />
    </div>

    <!-- HLS / native Video -->
    <div
      v-else-if="streamType === 'hls'"
      class="flex-1 flex items-center justify-center overflow-hidden"
    >
      <video
        :src="streamUrl"
        :style="{ objectFit: objectFit, ...containerStyle }"
        class="max-h-full max-w-full"
        autoplay
        muted
        playsinline
        @error="onImgError"
        @loadeddata="onImgLoad"
      />
    </div>

    <!-- Kein URL -->
    <div
      v-else
      class="flex-1 flex items-center justify-center text-gray-600 text-xs"
    >
      Keine URL
    </div>
  </div>
</template>
