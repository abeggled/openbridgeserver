<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PagePicker from '@/components/PagePicker.vue'
import {
  adapters,
  cameraStreams,
  STREAM_SOURCE_TYPES,
  type AdapterInstanceSummary,
  type StreamSourceCamera,
} from '@/api/client'
import { allStreams, defaultFullscreenSrc, defaultTileSrc } from './streamSource'

const props = defineProps<{
  modelValue: Record<string, unknown> | null | undefined
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

type AuthType = 'none' | 'basic' | 'apikey'

function normalizeAuthType(raw: unknown): AuthType {
  if (typeof raw !== 'string') return 'none'
  const v = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, '')
  if (v === 'basic' || v.startsWith('basicauth')) return 'basic'
  if (v === 'apikey' || v.startsWith('apikey')) return 'apikey'
  return 'none'
}

function raw(): Record<string, unknown> {
  const mv = props.modelValue
  return mv && typeof mv === 'object' && !Array.isArray(mv) ? mv : {}
}

const r = raw()
const label           = ref((r.label           as string)  ?? '')
const source          = ref<'url' | 'go2rtc'>(r.source === 'go2rtc' ? 'go2rtc' : 'url')
const streamInstanceId = ref((r.streamInstanceId as string) ?? '')
const streamCamera    = ref((r.streamCamera    as string)  ?? '')
const tileSrc         = ref((r.tileSrc         as string)  ?? '')
const fullscreenSrc   = ref((r.fullscreenSrc   as string)  ?? '')
const preferMode      = ref<'auto' | 'mse'>(r.preferMode === 'mse' ? 'mse' : 'auto')
const url             = ref((r.url             as string)  ?? '')
const streamType      = ref((r.streamType      as string)  ?? 'mjpeg')
const authType        = ref<AuthType>(normalizeAuthType(r.authType))
const username        = ref((r.username        as string)  ?? '')
const password        = ref((r.password        as string)  ?? '')
const apiKeyParam     = ref((r.apiKeyParam     as string)  ?? 'token')
const apiKeyValue     = ref((r.apiKeyValue     as string)  ?? '')
const refreshInterval = ref((r.refreshInterval as number)  ?? 5)
const aspectRatio     = ref((r.aspectRatio     as string)  ?? '16/9')
const objectFit       = ref((r.objectFit       as string)  ?? 'contain')
const useProxy        = ref((r.useProxy        as boolean) ?? false)
const clickAction     = ref((r.clickAction     as string)  ?? 'none')
// Ohne Aktion gibt es kein Ziel: eine versteckt mitgespeicherte Seite würde
// sonst beim späteren Wechsel auf „Zurück“ unbemerkt zur Ersatz-Seite.
const targetNodeId    = ref(clickAction.value === 'none' ? '' : (r.targetNodeId as string) ?? '')
watch(clickAction, (action) => { if (action === 'none') targetNodeId.value = '' })

watch(
  [
    label, source, streamInstanceId, streamCamera, tileSrc, fullscreenSrc, preferMode,
    url, streamType, authType, username, password, apiKeyParam, apiKeyValue, refreshInterval, aspectRatio, objectFit, useProxy, clickAction, targetNodeId,
  ],
  () => {
    emit('update:modelValue', {
      label:           label.value,
      source:          source.value,
      streamInstanceId: streamInstanceId.value,
      streamCamera:    streamCamera.value,
      tileSrc:         tileSrc.value,
      fullscreenSrc:   fullscreenSrc.value,
      preferMode:      preferMode.value,
      url:             url.value,
      streamType:      streamType.value,
      authType:        authType.value,
      username:        username.value,
      password:        password.value,
      apiKeyParam:     apiKeyParam.value,
      apiKeyValue:     apiKeyValue.value,
      refreshInterval: refreshInterval.value,
      aspectRatio:     aspectRatio.value,
      objectFit:       objectFit.value,
      useProxy:        useProxy.value,
      clickAction:     clickAction.value,
      targetNodeId:    targetNodeId.value,
    })
  },
)

const showBasicAuth  = computed(() => authType.value === 'basic')
const showApiKeyAuth = computed(() => authType.value === 'apikey')
const showRefresh    = computed(() => streamType.value === 'snapshot')

// ── Kamera-Server (go2rtc / Frigate, #1479) ─────────────────────────────────
const instances      = ref<AdapterInstanceSummary[]>([])
const instancesLoaded = ref(false)
const cameras        = ref<StreamSourceCamera[]>([])
const camerasLoading = ref(false)
const camerasError   = ref(false)

// Frigate gruppiert Haupt-/Substream je Kamera; bei go2rtc ist jeder Stream eine „Kamera“.
const groupsCameras = computed(() =>
  instances.value.find(i => i.id === streamInstanceId.value)?.adapter_type === 'FRIGATE',
)
const streamOptions = computed(() =>
  groupsCameras.value
    ? cameras.value.find(c => c.name === streamCamera.value)?.streams ?? []
    : allStreams(cameras.value),
)

async function loadInstances() {
  try {
    const all = await adapters.listInstances()
    instances.value = all.filter(i => STREAM_SOURCE_TYPES.includes(i.adapter_type))
  } catch {
    instances.value = []
  } finally {
    instancesLoaded.value = true
  }
}

async function loadCameras() {
  cameras.value = []
  camerasError.value = false
  if (!streamInstanceId.value) return
  camerasLoading.value = true
  try {
    cameras.value = await cameraStreams.cameras(streamInstanceId.value)
  } catch {
    camerasError.value = true
  } finally {
    camerasLoading.value = false
  }
}

function loadStreamSources() {
  if (!instancesLoaded.value) void loadInstances()
  void loadCameras()
}

onMounted(() => {
  if (source.value === 'go2rtc') loadStreamSources()
})
watch(source, (value) => {
  if (value === 'go2rtc') loadStreamSources()
})
// Nur echte Wechsel durch den Benutzer setzen die Auswahl darunter zurück.
watch(streamInstanceId, () => {
  streamCamera.value = ''
  tileSrc.value = ''
  fullscreenSrc.value = ''
  void loadCameras()
})
watch(streamCamera, () => {
  tileSrc.value = defaultTileSrc(streamOptions.value)
  fullscreenSrc.value = defaultFullscreenSrc(streamOptions.value)
})
watch(tileSrc, (value) => {
  if (value && !fullscreenSrc.value) fullscreenSrc.value = value
})
</script>

<template>
  <div class="space-y-3">

    <!-- Label -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="label"
        type="text"
        :placeholder="$t('widgets.kamera.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Quelle -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.source') }}</label>
      <select
        v-model="source"
        data-testid="kamera-config-source"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="url">{{ $t('widgets.kamera.sourceUrl') }}</option>
        <option value="go2rtc">{{ $t('widgets.kamera.sourceStreamServer') }}</option>
      </select>
    </div>

    <!-- Kamera-Server (go2rtc / Frigate) -->
    <template v-if="source === 'go2rtc'">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.streamInstance') }}</label>
        <select
          v-model="streamInstanceId"
          data-testid="kamera-config-instance"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="">{{ $t('widgets.kamera.choose') }}</option>
          <option v-for="inst in instances" :key="inst.id" :value="inst.id">
            {{ inst.name }} ({{ inst.adapter_type }})
          </option>
        </select>
        <p v-if="instancesLoaded && !instances.length" class="text-xs text-yellow-600 mt-1" data-testid="kamera-config-no-instances">
          {{ $t('widgets.kamera.noStreamInstances') }}
        </p>
      </div>

      <p v-if="camerasLoading" class="text-xs text-gray-500">{{ $t('widgets.kamera.loadingCameras') }}</p>
      <p v-else-if="camerasError" class="text-xs text-red-400" data-testid="kamera-config-cameras-error">
        {{ $t('widgets.kamera.camerasUnavailable') }}
      </p>

      <div v-if="groupsCameras">
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.camera') }}</label>
        <select
          v-model="streamCamera"
          data-testid="kamera-config-camera"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="">{{ $t('widgets.kamera.choose') }}</option>
          <option v-for="cam in cameras" :key="cam.name" :value="cam.name">{{ cam.name }}</option>
        </select>
      </div>

      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.tileStream') }}</label>
          <select
            v-model="tileSrc"
            data-testid="kamera-config-tile-src"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="">{{ $t('widgets.kamera.choose') }}</option>
            <option v-for="st in streamOptions" :key="st.src" :value="st.src">{{ st.label }}</option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.fullscreenStream') }}</label>
          <select
            v-model="fullscreenSrc"
            data-testid="kamera-config-fullscreen-src"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="">{{ $t('widgets.kamera.choose') }}</option>
            <option v-for="st in streamOptions" :key="st.src" :value="st.src">{{ st.label }}</option>
          </select>
        </div>
      </div>

      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.preferMode') }}</label>
        <select
          v-model="preferMode"
          data-testid="kamera-config-prefer-mode"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="auto">{{ $t('widgets.kamera.preferModeAuto') }}</option>
          <option value="mse">{{ $t('widgets.kamera.preferModeMse') }}</option>
        </select>
        <p class="text-xs text-gray-500 mt-1">{{ $t('widgets.kamera.preferModeHint') }}</p>
      </div>
    </template>

    <template v-else>
    <!-- Stream-Typ -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.streamType') }}</label>
      <select
        v-model="streamType"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="mjpeg">{{ $t('widgets.kamera.streamMjpeg') }}</option>
        <option value="snapshot">{{ $t('widgets.kamera.streamSnapshot') }}</option>
        <option value="hls">{{ $t('widgets.kamera.streamHls') }}</option>
      </select>
    </div>

    <!-- URL -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.streamUrl') }}</label>
      <input
        v-model="url"
        type="text"
        placeholder="http://192.168.1.100/video.cgi"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Snapshot Refresh-Intervall -->
    <div v-if="showRefresh">
      <label class="block text-xs text-gray-400 mb-1">
        {{ $t('widgets.kamera.refreshInterval') }}
      </label>
      <input
        v-model.number="refreshInterval"
        type="number"
        min="1"
        max="3600"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Authentifizierung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.auth') }}</label>
      <select
        v-model="authType"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="none">{{ $t('widgets.kamera.authNone') }}</option>
        <option value="basic">{{ $t('widgets.kamera.authBasic') }}</option>
        <option value="apikey">{{ $t('widgets.kamera.authApiKey') }}</option>
      </select>
    </div>

    <!-- Basic Auth -->
    <template v-if="showBasicAuth">
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.username') }}</label>
          <input
            v-model="username"
            type="text"
            autocomplete="off"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.password') }}</label>
          <input
            v-model="password"
            type="password"
            autocomplete="new-password"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
      <p class="text-xs text-yellow-600">
        {{ $t('widgets.kamera.credentialWarning') }}
      </p>
    </template>

    <!-- API Key -->
    <template v-if="showApiKeyAuth">
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.apiKeyParam') }}</label>
          <input
            v-model="apiKeyParam"
            type="text"
            placeholder="token"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.apiKey') }}</label>
          <input
            v-model="apiKeyValue"
            type="password"
            autocomplete="new-password"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </template>

    <!-- Proxy -->
    <div class="flex items-center gap-2">
      <input
        id="cam-proxy"
        v-model="useProxy"
        type="checkbox"
        class="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
      />
      <label for="cam-proxy" class="text-xs text-gray-300 cursor-pointer">
        {{ $t('widgets.kamera.useProxy') }}
        <span class="text-gray-500 font-normal ml-1">{{ $t('widgets.kamera.proxyMixedContentHint') }}</span>
      </label>
    </div>
    </template>

    <!-- Darstellung -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.aspectRatio') }}</label>
        <select
          v-model="aspectRatio"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="16/9">16:9</option>
          <option value="4/3">4:3</option>
          <option value="1/1">{{ $t('widgets.kamera.aspectSquare') }}</option>
          <option value="free">{{ $t('widgets.kamera.aspectFree') }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.objectFit') }}</label>
        <select
          v-model="objectFit"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="contain">{{ $t('widgets.kamera.fitContain') }}</option>
          <option value="cover">{{ $t('widgets.kamera.fitCover') }}</option>
          <option value="fill">{{ $t('widgets.kamera.fitFill') }}</option>
        </select>
      </div>
    </div>

    <!-- Antippen: Seite öffnen oder zurück -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.clickAction') }}</label>
      <select
        v-model="clickAction"
        data-testid="kamera-config-click-action"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="none">{{ $t('widgets.kamera.clickActionNone') }}</option>
        <option value="navigate">{{ $t('widgets.kamera.clickActionNavigate') }}</option>
        <option value="back">{{ $t('widgets.kamera.clickActionBack') }}</option>
      </select>
    </div>

    <div v-if="clickAction !== 'none'" data-testid="kamera-config-click-target">
      <label class="block text-xs text-gray-400 mb-1">
        {{ clickAction === 'back' ? $t('widgets.kamera.clickFallbackPage') : $t('widgets.kamera.clickTargetPage') }}
      </label>
      <PagePicker v-model="targetNodeId" />
      <p v-if="clickAction === 'back'" class="text-xs text-gray-500 mt-1">
        {{ $t('widgets.kamera.clickFallbackHint') }}
      </p>
    </div>

  </div>
</template>
