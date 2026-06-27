<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

const props = defineProps<{
  modelValue: Record<string, unknown> | null | undefined
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

type AuthType = 'none' | 'basic' | 'apikey'

interface CameraConfig {
  label: string
  url: string
  streamType: string
  authType: AuthType
  username: string
  password: string
  apiKeyParam: string
  apiKeyValue: string
  refreshInterval: number
  aspectRatio: string
  objectFit: string
  useProxy: boolean
}

function asRecord(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
}

function stringValue(raw: unknown, fallback = ''): string {
  return typeof raw === 'string' ? raw : fallback
}

function numberValue(raw: unknown, fallback: number): number {
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : fallback
}

function booleanValue(raw: unknown, fallback: boolean): boolean {
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'string') {
    const value = raw.trim().toLowerCase()
    if (value === 'true') return true
    if (value === 'false') return false
  }
  return fallback
}

function normalizeAuthType(raw: unknown): AuthType {
  if (typeof raw !== 'string') return 'none'
  const value = raw.trim().toLowerCase().replace(/[\s_-]+/g, '')
  if (value === 'basic' || value === 'basicauth') return 'basic'
  if (value === 'apikey' || value === 'api' || value === 'token') return 'apikey'
  return 'none'
}

function parseConfig(raw: unknown): CameraConfig {
  const value = asRecord(raw)
  return {
    label: stringValue(value.label),
    url: stringValue(value.url),
    streamType: stringValue(value.streamType, 'mjpeg'),
    authType: normalizeAuthType(value.authType ?? value.auth_type ?? value.auth),
    username: stringValue(value.username),
    password: stringValue(value.password),
    apiKeyParam: stringValue(value.apiKeyParam ?? value.api_key_param, 'token'),
    apiKeyValue: stringValue(value.apiKeyValue ?? value.api_key_value),
    refreshInterval: numberValue(value.refreshInterval ?? value.refresh_interval, 5),
    aspectRatio: stringValue(value.aspectRatio ?? value.aspect_ratio, '16/9'),
    objectFit: stringValue(value.objectFit ?? value.object_fit, 'contain'),
    useProxy: booleanValue(value.useProxy ?? value.use_proxy, false),
  }
}

const cfg = reactive<CameraConfig>(parseConfig(props.modelValue))

let syncingFromProps = false

watch(
  () => props.modelValue,
  (value) => {
    syncingFromProps = true
    Object.assign(cfg, parseConfig(value))
    syncingFromProps = false
  },
)

watch(
  cfg,
  () => {
    if (!syncingFromProps) emit('update:modelValue', { ...cfg })
  },
  { deep: true, flush: 'sync' },
)

const showBasicAuth  = computed(() => cfg.authType === 'basic')
const showApiKeyAuth = computed(() => cfg.authType === 'apikey')
const showRefresh    = computed(() => cfg.streamType === 'snapshot')
</script>

<template>
  <div class="space-y-3">

    <!-- Label -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Eingang, Garten …"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Stream-Typ -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.streamType') }}</label>
      <select
        v-model="cfg.streamType"
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
        v-model="cfg.url"
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
        v-model.number="cfg.refreshInterval"
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
        v-model="cfg.authType"
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
            v-model="cfg.username"
            type="text"
            autocomplete="off"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.password') }}</label>
          <input
            v-model="cfg.password"
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
            v-model="cfg.apiKeyParam"
            type="text"
            placeholder="token"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.apiKey') }}</label>
          <input
            v-model="cfg.apiKeyValue"
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
        v-model="cfg.useProxy"
        type="checkbox"
        class="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
      />
      <label for="cam-proxy" class="text-xs text-gray-300 cursor-pointer">
        {{ $t('widgets.kamera.useProxy') }}
        <span class="text-gray-500 font-normal ml-1">(Mixed-Content / HTTPS → HTTP)</span>
      </label>
    </div>

    <!-- Darstellung -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.kamera.aspectRatio') }}</label>
        <select
          v-model="cfg.aspectRatio"
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
          v-model="cfg.objectFit"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="contain">{{ $t('widgets.kamera.fitContain') }}</option>
          <option value="cover">{{ $t('widgets.kamera.fitCover') }}</option>
          <option value="fill">{{ $t('widgets.kamera.fitFill') }}</option>
        </select>
      </div>
    </div>

  </div>
</template>
